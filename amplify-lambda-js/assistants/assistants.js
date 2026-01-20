//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {newStatus} from "../common/status.js";
import {sendStateEventToStream, sendStatusEventToStream, forceFlush} from "../common/streams.js";
import {csvAssistant} from "./csv.js";
import {getLogger} from "../common/logging.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";
import { getTokenCount } from "../datasource/datasources.js";
// import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { codeInterpreterAssistant } from "./codeInterpreter.js";
import {fillInAssistant, getUserDefinedAssistant} from "./userDefinedAssistants.js";
import { mapReduceAssistant } from "./mapReduceAssistant.js";
import { ArtifactModeAssistant } from "./ArtifactModeAssistant.js";
import { agentInstructions, getTools } from "./agent.js"
import { executeToolLoop, shouldEnableWebSearch } from "../tools/toolLoop.js";
import { getAdminWebSearchApiKey } from "../tools/webSearch.js";

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

        const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
        // ✅ Use token counting
        const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCount(ds, model), 0);
        const aboveLimit = requiredTokens >= limit;

        logger.debug(`Model: ${model.id}, tokenLimit: ${model.inputContextWindow}`)
        logger.debug(`RAG Only: ${body.options.ragOnly}, dataSources: ${dataSources.length}`)
        logger.debug(`Required tokens: ${requiredTokens}, limit: ${limit}, aboveLimit: ${aboveLimit}`);

        if (params.blockTerminator) {
            body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
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
            (params.body?.imageSources && params.body.imageSources.length > 0);

        logger.info("🎯 Assistant decision logic:", {
            ragOnly: body.options.ragOnly,
            aboveLimit,
            dataSources_length: dataSources.length,
            hasPreResolvedData,
            needsDataProcessing: needsDataProcessingDecision,
            enableWebSearch: body?.enableWebSearch,
            route: !body.options.ragOnly && !body.options?.assistantId && aboveLimit ? "mapReduce" :
                   needsDataProcessingDecision && !body.options.ragOnly ? "chatWithData" : "directLLM"
        });
        
        // 🚨 CRITICAL: User-defined assistants NEVER use mapReduce - always RAG for source visibility
        const isUserDefinedAssistant = !!body.options?.assistantId;
        
        if (!body.options.ragOnly && !isUserDefinedAssistant && aboveLimit){
            logger.info("→ Using mapReduceAssistant (token limit exceeded)");
            
            
            
            return mapReduceAssistant.handler(params, body, dataSources, responseStream);
        } else {
            if (needsDataProcessingDecision) {
                // Use chatWithDataStateless for RAG, document processing, conversation discovery  
                logger.info("→ Using chatWithDataStateless (has data sources or conversation discovery)");
                const {chatWithDataStateless} = await import("../common/chatWithData.js");
                
                // 🚀 PERFORMANCE: Use pre-resolved data sources if available to avoid duplicate getDataSourcesByUse() calls
                const enhancedParams = params.preResolvedDataSourcesByUse ? {
                    ...params,
                    preResolvedDataSourcesByUse: params.preResolvedDataSourcesByUse
                } : params;
                
                // ✅ USE ROUTER'S MODIFIED BODY: params.body contains imageSources from resolveDataSources()
                const bodyWithImages = {...body, imageSources: params.body?.imageSources || undefined};
                return chatWithDataStateless(enhancedParams, model, bodyWithImages, dataSources, responseStream);
            } else {
                // Direct LLM call for simple conversations
                logger.info("→ Using direct native provider (no data sources needed)");
                // ✅ USE ROUTER'S MODIFIED BODY: params.body contains imageSources from resolveDataSources()
                const bodyWithImages = {...body, imageSources: params.body?.imageSources || undefined};

                // Check if web search or MCP is enabled
                let webSearchEnabled = shouldEnableWebSearch(body);
                // mcpEnabled can be at top level OR in options (frontend sends it in options via vendorProps)
                const mcpEnabled = body?.mcpEnabled === true || body?.options?.mcpEnabled === true;

                // Also check for admin-configured web search (auto-enable ONLY if frontend didn't explicitly set a preference)
                let adminWebSearchAvailable = false;
                // Check if frontend explicitly disabled web search (respect explicit false)
                const frontendExplicitlyDisabled = body?.enableWebSearch === false ||
                                                  body?.options?.enableWebSearch === false ||
                                                  body?.options?.options?.webSearch === false;

                // Only auto-enable if frontend didn't explicitly set a preference (undefined values)
                if (!webSearchEnabled && !frontendExplicitlyDisabled) {
                    try {
                        const adminKey = await getAdminWebSearchApiKey();
                        if (adminKey && adminKey.provider && adminKey.api_key) {
                            adminWebSearchAvailable = true;
                            webSearchEnabled = true;
                            logger.info(`Admin web search available (${adminKey.provider}), auto-enabling tool loop`);
                        }
                    } catch (error) {
                        logger.debug('Failed to check admin web search config:', error.message);
                    }
                } else if (frontendExplicitlyDisabled) {
                    logger.info(`Web search explicitly disabled by frontend, respecting user choice`);
                }

                logger.info("🔍 Tool loop check:", {
                    enableWebSearch: body?.enableWebSearch,
                    optionsEnableWebSearch: body?.options?.enableWebSearch,
                    optionsOptionsWebSearch: body?.options?.options?.webSearch,
                    frontendExplicitlyDisabled,
                    adminWebSearchAvailable,
                    webSearchEnabled,
                    mcpEnabled,
                    optionsMcpEnabled: body?.options?.mcpEnabled,
                    toolsCount: (body?.tools || body?.options?.tools)?.length || 0
                });

                if (webSearchEnabled || mcpEnabled) {
                    logger.info(`→ Tool loop enabled (webSearch: ${webSearchEnabled}, mcp: ${mcpEnabled})`);
                    return await executeToolLoop(
                        {
                            account: params.account,
                            options: {
                                ...bodyWithImages.options,
                                model,
                                requestId: params.options?.requestId
                            }
                        },
                        bodyWithImages.messages,
                        model,
                        responseStream,
                        {
                            max_tokens: bodyWithImages.max_tokens || 2000,
                            imageSources: bodyWithImages.imageSources,
                            // MCP tools sent from frontend need client-side execution
                            // since they run on the user's local machine
                            mcpClientSide: mcpEnabled,
                            // Pass through any tools from the frontend (can be at top level or in options)
                            tools: bodyWithImages.tools || bodyWithImages.options?.tools
                        }
                    );
                }

                return await callUnifiedLLM(
                    {
                        account: params.account,
                        options: {
                            ...bodyWithImages.options,  // Include all options from body (including trackConversations)
                            model,
                            requestId: params.options?.requestId
                        }
                    },
                    bodyWithImages.messages,
                    responseStream,
                    {
                        max_tokens: bodyWithImages.max_tokens || 2000,
                        imageSources: bodyWithImages.imageSources  // ✅ FIX: Pass imageSources through options
                    }
                );
            }
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
        
        selectedAssistant = await getUserDefinedAssistant(user, defaultAssistant, clientSelectedAssistant, token);
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
    if (selectedAssistant.disclaimer) stateInfo = {...stateInfo, currentAssistantDisclaimer : selectedAssistant.disclaimer};
    sendStateEventToStream(responseStream, stateInfo);

    // Note: "Assistant is responding" status message moved to router (after smart messages)

    forceFlush(responseStream);

    return selected;
}
