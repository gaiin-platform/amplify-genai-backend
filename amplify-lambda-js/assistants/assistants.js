//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {newStatus} from "../common/status.js";
import {sendStateEventToStream, sendStatusEventToStream, forceFlush} from "../common/streams.js";
import {csvAssistant} from "./csv.js";
import {getLogger} from "../common/logging.js";
import { callLiteLLM } from "../litellm/litellmClient.js";
import { getTokenCountOptimized } from "../common/optimizedDataSources.js";
// import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { codeInterpreterAssistant } from "./codeInterpreter.js";
import {fillInAssistant, getUserDefinedAssistant} from "./userDefinedAssistants.js";
import { mapReduceAssistant } from "./mapReduceAssistant.js";
import { ArtifactModeAssistant } from "./ArtifactModeAssistant.js";
import { agentInstructions, getTools } from "./agent.js"

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

                        // already ensures model has been mapped to our backend version in router
        const model = (body.options && body.options.model) ? body.options.model : params.model;

        logger.debug("Using model: ", model);

        const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
        // ✅ PERFORMANCE OPTIMIZATION: Use cached token counting
        const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCountOptimized(ds, model), 0);
        const aboveLimit = requiredTokens >= limit;

        logger.debug(`Model: ${model.id}, tokenLimit: ${model.inputContextWindow}`)
        logger.debug(`RAG Only: ${body.options.ragOnly}, dataSources: ${dataSources.length}`)
        logger.debug(`Required tokens: ${requiredTokens}, limit: ${limit}, aboveLimit: ${aboveLimit}`);

        if (params.blockTerminator) {
            body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
        }

        if (!body.options.ragOnly && aboveLimit){
            return mapReduceAssistant.handler(params, body, dataSources, responseStream);
        } else if (dataSources.length > 0 && !body.options.ragOnly) {
            // ✅ Use chatWithData for RAG processing with data sources
            const {chatWithDataStateless} = await import("../common/chatWithData.js");
            return chatWithDataStateless(params, model, body, dataSources, responseStream);
        } else {
            // ✅ Direct LiteLLM call when no RAG needed (ragOnly mode or no data sources)
            return await callLiteLLM(body, model, params.account, responseStream, [], true);
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





export const chooseAssistantForRequest = async (account, model, body, dataSources, responseStream) => {
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
        console.log("ARTIFACT MODE DETERMINED")
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

    sendStatusEventToStream(responseStream, newStatus(
        {
            inProgress: false,
            message: "The \"" + selected.displayName + " Assistant\" is responding.",
            icon: "assistant",
            sticky: true
        }));
    forceFlush(responseStream);

    return selected;
}
