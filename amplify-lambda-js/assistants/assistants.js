//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { newStatus } from "../common/status.js";
import { sendStateEventToStream, sendStatusEventToStream, forceFlush } from "../common/streams.js";
import { csvAssistant } from "./csv.js";
import { getLogger } from "../common/logging.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";
import { getTokenCount } from "../datasource/datasources.js";
// import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { codeInterpreterAssistant } from "./codeInterpreter.js";
import {fillInAssistant, getUserDefinedAssistant} from "./userDefinedAssistants.js";
import { isLayeredAssistantId, resolveLayeredAssistant } from "./layeredAssistantRouter.js";
import { mapReduceAssistant } from "./mapReduceAssistant.js";
import { ArtifactModeAssistant } from "./ArtifactModeAssistant.js";
import { agentInstructions, getTools } from "./agent.js"
import { executeToolLoop, shouldEnableWebSearch } from "../tools/toolLoop.js";
import { getAdminWebSearchApiKey } from "../tools/webSearch.js";
import { chatWithDataStateless } from "../common/chatWithData.js";

const logger = getLogger("assistants");



const defaultAssistant = {
    name: "default",
    displayName: "Amplify",
    handlesDataSources: (ds) => {
        return true;
    },
    handlesModel: (model) => {
        return true;
    },
    description: "Default assistant that can handle arbitrary requests with any data type but may " +
        "not be as good as a specialized assistant.",
    handler: async (params, body, dataSources, responseStream) => {
        logger.debug("🎯 Assistant: Received datasources:", {
            dataSources_length: dataSources?.length || 0,
            dataSources_ids: dataSources?.map(ds => ds.id?.substring(0, 50))
        });

        // already ensures model has been mapped to our backend version in router
        const model = (body.options && body.options.model) ? body.options.model : params.model;

        logger.debug("Using model: ", model);

        // 🚫 DEPRECATED: Token limit calculation for mapReduce routing
        // Now handled by 85% split logic in chatWithData.js
        // const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
        // const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCount(ds, model), 0);
        // const aboveLimit = requiredTokens != 0 && requiredTokens >= limit;

        logger.debug(`Model: ${model.id}, tokenLimit: ${model.inputContextWindow}`);
        logger.debug(`RAG Only: ${body.options.ragOnly}, dataSources: ${dataSources.length}`);

        if (params.blockTerminator) {
            body = { ...body, options: { ...body.options, blockTerminator: params.blockTerminator } };
        }

        // 🚀 SMART ROUTING: Use pre-resolved data sources from router to make routing decision
        const preResolvedSources = params.preResolvedDataSourcesByUse;
        const hasPreResolvedData = preResolvedSources && (
            (preResolvedSources.ragDataSources && preResolvedSources.ragDataSources.length > 0) ||
            (preResolvedSources.dataSources && preResolvedSources.dataSources.length > 0) ||
            (preResolvedSources.conversationDataSources && preResolvedSources.conversationDataSources.length > 0) ||
            (preResolvedSources.attachedDataSources && preResolvedSources.attachedDataSources.length > 0)
        );

        // 🚨 CRITICAL: ALWAYS use RAG pipeline if ANY data sources exist (pre-resolved OR raw)
        // This fixes user-defined assistants that don't provide preResolvedSources
        const needsDataProcessingDecision = hasPreResolvedData ||
            (dataSources && dataSources.length > 0) ||
            (body.imageSources && body.imageSources.length > 0) ||
            (params.body?.imageSources && params.body.imageSources.length > 0) ||
            (body.videoSources && body.videoSources.length > 0) ||
            (params.body?.videoSources && params.body.videoSources.length > 0);

        logger.info("🎯 Assistant decision logic:", {
            ragOnly: body.options.ragOnly,
            dataSources_length: dataSources.length,
            hasPreResolvedData,
            needsDataProcessing: needsDataProcessingDecision,
            enableWebSearch: body?.options?.enableWebSearch,
            route: needsDataProcessingDecision && !body.options.ragOnly ? "chatWithData" : "directLLM"
        });


        if (needsDataProcessingDecision) {
            // Use chatWithDataStateless for RAG, document processing, conversation discovery  
            logger.info("→ Using chatWithDataStateless (has data sources or conversation discovery)");


            // 🚀 PERFORMANCE: Use pre-resolved data sources if available to avoid duplicate getDataSourcesByUse() calls
            // Fallback to empty structure when router returned null (e.g. image-only requests with no dataSources/conversationId)
            const enhancedParams = {
                ...params,
                options: {
                    ...params.options,
                    ...body.options  // Merge body.options to include trackConversations and other flags
                },
                preResolvedDataSourcesByUse: params.preResolvedDataSourcesByUse ?? {
                    dataSources: [],
                    ragDataSources: [],
                    conversationDataSources: [],
                    attachedDataSources: [],
                    groupDataSources: [],
                    astDataSources: []
                }
            };

            // ✅ USE ROUTER'S MODIFIED BODY: params.body contains imageSources/videoSources from resolveDataSources()
            const bodyWithMedia = { ...body, imageSources: params.body?.imageSources || undefined, videoSources: params.body?.videoSources || undefined };
            return chatWithDataStateless(enhancedParams, model, bodyWithMedia, dataSources, responseStream);
        } else {
            // Direct LLM call for simple conversations
            logger.info("→ Using direct native provider (no data sources needed)");
            // ✅ USE ROUTER'S MODIFIED BODY: params.body contains imageSources/videoSources from resolveDataSources()
            const bodyWithMedia = { ...body, imageSources: params.body?.imageSources || undefined, videoSources: params.body?.videoSources || undefined };

            // Check if web search or MCP is enabled
            let webSearchEnabled = shouldEnableWebSearch(body);
            // mcpEnabled can be at top level OR in options (frontend sends it in options via vendorProps)
            const mcpEnabled = body?.mcpEnabled === true || body?.options?.mcpEnabled === true;

            if (webSearchEnabled || mcpEnabled) {
                logger.info(`→ Tool loop enabled (webSearch: ${webSearchEnabled}, mcp: ${mcpEnabled})`);
                return await executeToolLoop(
                    {
                        account: params.account,
                        options: {
                            ...bodyWithMedia.options,
                            model,
                            requestId: params.options?.requestId
                        }
                    },
                    bodyWithMedia.messages,
                    model,
                    responseStream,
                    {
                        max_tokens: bodyWithMedia.max_tokens || 2000,
                        imageSources: bodyWithMedia.imageSources,
                        // MCP tools sent from frontend need client-side execution
                        // since they run on the user's local machine
                        mcpClientSide: mcpEnabled,
                        // Pass through any tools from the frontend (can be at top level or in options)
                        tools: bodyWithMedia.tools || bodyWithMedia.options?.tools,
                        webSearchEnabled: webSearchEnabled,
                    },
                );
            }

            return await callUnifiedLLM(
                {
                    account: params.account,
                    options: {
                        ...bodyWithMedia.options,  // Include all options from body (including trackConversations)
                        model,
                        requestId: params.options?.requestId
                    }
                },
                bodyWithMedia.messages,
                responseStream,
                {
                    max_tokens: bodyWithMedia.max_tokens || 2000,
                    imageSources: bodyWithMedia.imageSources,  // ✅ FIX: Pass imageSources through options
                    videoSources: bodyWithMedia.videoSources   // ✅ FIX: Pass videoSources through options
                }
            );

        }
    }
};



export const defaultAssistants = [
    defaultAssistant,
    //documentAssistant,
    //reportWriterAssistant,
    // csvAssistant,
    //documentSearchAssistant
    //mapReduceAssistant
];

export const buildDataSourceDescriptionMessages = (dataSources) => {
    if (!dataSources || dataSources.length === 0) {
        return "";
    }

    const descriptions = dataSources.map((ds) => {
        return `${ds.id}: (${ds.type})`;
    }).join("\n");

    return `
    The following data sources are available for the task:
    ---------------
    ${descriptions}
    --------------- 
    `;
}

export const buildAssistantDescriptionMessages = (assistants) => {
    if (!assistants || assistants.length === 0) {
        return [];
    }

    const descriptions = assistants.map((assistant) => {
        return `name: ${assistant.name} - ${assistant.description}`;
    }).join("\n");

    return `
    The following assistants are available to work on the task:
    ---------------
    ${descriptions}
    --------------- 
    `;
}





export const chooseAssistantForRequest = async (account, _model, body, _dataSources, responseStream) => {
    // 🚀 BREAKTHROUGH: Direct streaming without LLM dependency
    logger.info(`Choose Assistant for Request `);

    const clientSelectedAssistant = body.options?.assistantId ?? null;

    let selectedAssistant = null;
    if (clientSelectedAssistant) {
        logger.info(`Client Selected Assistant: `, clientSelectedAssistant);
        // For group assistants
        const user = account.user;
        const token = account.accessToken;

        // ── Layered Assistant routing ──────────────────────────────────────────
        // If the selected ID is a layered assistant (astr/ or astgr/), resolve it
        // down to a concrete leaf assistant before the normal lookup flow.
        let resolvedAssistantId = clientSelectedAssistant;
        if (isLayeredAssistantId(clientSelectedAssistant)) {
            logger.info(`Layered assistant detected: ${clientSelectedAssistant} — resolving via tree routing`);
            const leafId = await resolveLayeredAssistant(
                account,
                _model,
                body,
                clientSelectedAssistant,
                responseStream
            );
            if (!leafId) {
                // resolveLayeredAssistant already sent appropriate status to stream
                if (body.options.api_accessed) {
                    throw new Error("Provided Layered Assistant ID is invalid, inaccessible, or could not be routed.");
                }
                // Leave selectedAssistant as null — falls through to defaultAssistant below
                resolvedAssistantId = null;
            } else {
                resolvedAssistantId = leafId;
                logger.info(`Layered assistant resolved to leaf: ${leafId}`);
            }
        }
        // ──────────────────────────────────────────────────────────────────────

        if (resolvedAssistantId) {
            selectedAssistant = await getUserDefinedAssistant(user, defaultAssistant, resolvedAssistantId, token);
            if (!selectedAssistant) {
                sendStatusEventToStream(responseStream, newStatus(
                    {   inProgress: false,
                        message: "Selected Assistant Not Found",
                        icon: "assistant",
                        sticky: true
                    }));
                forceFlush(responseStream);

                if (body.options.api_accessed) {
                    throw new Error("Provided Assistant ID is invalid or user does not have access to this assistant.");
                }
            }
        }
    } else if (getTools(body.messages).length > 0) {
        logger.info("Using tools");
        // Note: tools are added in fillInAssistant in order to support tool use for client selected Assistants

        selectedAssistant = fillInAssistant(
            {
                name: "Amplify Automation",
                instructions: agentInstructions,
                description: "Amplify Automation",
                data: {
                    opsLanguageVersion: "v4",
                }
            },
            defaultAssistant
        )

    } else if (body.options.codeInterpreterOnly && (!body.options.api_accessed)) {
        selectedAssistant = await codeInterpreterAssistant(defaultAssistant);
        //codeInterpreterAssistant;
    } else if (body.options.artifactsMode && (!body.options.api_accessed)) {
        selectedAssistant = ArtifactModeAssistant;
        logger.info("ARTIFACT MODE DETERMINED")
    }


    if (selectedAssistant === null) {
        selectedAssistant = defaultAssistant;
    }

    const selected = selectedAssistant || defaultAssistant;

    logger.info("Sending State Event to Stream ", selectedAssistant.name);
    let stateInfo = {
        currentAssistant: selectedAssistant.name,
        currentAssistantId: clientSelectedAssistant || selectedAssistant.name,
    }
    if (selectedAssistant.disclaimer) stateInfo = { ...stateInfo, currentAssistantDisclaimer: selectedAssistant.disclaimer };
    sendStateEventToStream(responseStream, stateInfo);

    // Note: "Assistant is responding" status message moved to router (after smart messages)

    forceFlush(responseStream);

    return selected;
}
