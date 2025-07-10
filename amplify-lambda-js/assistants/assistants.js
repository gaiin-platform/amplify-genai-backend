//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {newStatus} from "../common/status.js";
import {csvAssistant} from "./csv.js";
import {getLogger} from "../common/logging.js";
import {getChatFn, ModelTypes, getModelByType, isOpenAIModel} from "../common/params.js";
import {reportWriterAssistant} from "./reportWriter.js";
import {documentAssistant} from "./documents.js";
// import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { codeInterpreterAssistant } from "./codeInterpreter.js";
import {sendDeltaToStream} from "../common/streams.js";
import {createChatTask, sendAssistantTaskToQueue} from "./queue/messages.js";
import { v4 as uuidv4 } from 'uuid';
import {getDataSourcesByUse, isImage} from "../datasource/datasources.js";
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
    handler: async (llm, params, body, ds, responseStream) => {

                        // already ensures model has been mapped to our backend version in router
        const model = (body.options && body.options.model) ? body.options.model : params.model;

        logger.debug("Using model: ", model);

        const {dataSources} = await getDataSourcesByUse(params, body, ds);

        const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
        const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCount(ds, model), 0);
        const aboveLimit = requiredTokens >= limit;

        logger.debug(`Model: ${model.id}, tokenLimit: ${model.inputContextWindow}`)
        logger.debug(`RAG Only: ${body.options.ragOnly}, dataSources: ${dataSources.length}`)
        logger.debug(`Required tokens: ${requiredTokens}, limit: ${limit}, aboveLimit: ${aboveLimit}`);

        if (params.blockTerminator) {
            body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
        }

        if (!body.options.ragOnly && aboveLimit){
            return mapReduceAssistant.handler(llm, params, body, dataSources, responseStream);
        } else {
            return llm.prompt(body, dataSources);
        }
    }
};

const batchAssistant = {
    name: "batch",
    displayName: "Batch",
    handlesDataSources: (ds) => {
        return true;
    },
    handlesModel: (model) => {
        return true;
    },
    description: "This assistant is used to queue messages for assistants and isn't normally used.",
    handler: async (llm, params, body, dataSources, responseStream) => {
        try{

            // Date in MM-DD-YYYY format
            const date = new Date().toISOString().replace(/:/g, "-").split("T")[0];

            // Time in HH-MM-SS format
            const time = new Date().toISOString().replace(/:/g, "-").split("T")[1].split(".")[0];

            const updatedBody = {
                ...body,
                messages: [
                    ...body.messages.slice(0,-1),
                    {
                        "role": "user",
                        "content": body.messages.slice(-1)[0].content.split(":")[1].trim()
                    }
                ]
            }

            const task = createChatTask(
                params.account.accessToken,
                params.account.user,
                `${params.account.user}/tasks/${date}/chat-${time}-${uuidv4()}.json`,
                updatedBody,
                getModelByType(params, ModelTypes.CHEAPEST),
                params.options,
            )
            await sendAssistantTaskToQueue(task);
            sendDeltaToStream(responseStream, "answer","Message queued.");
        }
        catch(e){
            logger.error("Error sending assistant task to queue", e);
            sendDeltaToStream(responseStream, "answer", "Error sending assistant task to queue");
        }

        responseStream.end();
    }
};

// These assistants should NOT be in the list
// right now
//documentSearchAssistant
//mapReduceAssistant
//batchAssistant,
//documentAssistant,
//reportWriterAssistant,
//csvAssistant,

export const defaultAssistants = [
    defaultAssistant,
    //batchAssistant,
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

export const chooseAssistantForRequestWithLLM = async (llm, body, dataSources, assistants = defaultAssistants) => {
    // console.log(chooseAssistantForRequestWithLLM);

    const messages = [
        {
            "role": "system",
            "content": `
            Help the user choose the best assistant for the task.
            You only need to output the name of the assistant. YOU MUST
            honor the user's choice if they request a specific assistant.
            `
        },
        // {
        //     "role": "user",
        //     "content": `
        //     Think step by step how to perform the task. What are the steps?
        //     Which assistant is the best fit to solve the given task based on the
        //     steps? Is the user asking for a specific assistant?
        //
        //     If you are not sure, please choose the default assistant.
        //
        //     ${buildAssistantDescriptionMessages(assistants)}
        //     ${buildDataSourceDescriptionMessages(dataSources)}
        //
        //     Please choose the best assistant to help with the task:
        //     ---------------
        //     ${body.messages.slice(-1)[0].content}
        //     ---------------
        //     `
        // },

    ];

    const prompt = `
Think step by step how to perform the task. What are the steps? 
Which assistant is the best fit to solve the given task based on the
steps? Is the user asking for a specific assistant?

If you are not sure, please choose the default assistant.

${buildAssistantDescriptionMessages(assistants)}
${buildDataSourceDescriptionMessages(dataSources)}

Please choose the best assistant to help with the task:
---------------
${body.messages.slice(-1)[0].content}
---------------
`;
    const model = body.options.advancedModel;
    const updatedBody = {messages, options:{ model }};

    const names = assistants.map((a) => a.name);

    const chatFn = async (body, writable, context) => {
        return await getChatFn(model, body, writable, context);
    }
    const llmClone = llm.clone(chatFn);

    //return await llm.promptForChoice({messages, options:{model}}, names, []);
    const result = await llmClone.promptForData(updatedBody, [], prompt,
        {bestAssistant:names.join("|")}, null, (r) => {
       return r.bestAssistant && assistants.find((a) => a.name === r.bestAssistant);
    }, 3);

    return result.bestAssistant || defaultAssistant.name;
}

const getTokenCount = (dataSource, model) => {
    if (dataSource.metadata && dataSource.metadata.totalTokens ) {
        const totalTokens = dataSource.metadata.totalTokens;
        if (isImage(dataSource)) {
            return isOpenAIModel (model.id) ? totalTokens.gpt : 
                 model.id.includes("anthropic") ? totalTokens.claude : 1000;
        }
        if (!dataSource.metadata.ragOnly) return totalTokens;
    }
    else if(dataSource.metadata && dataSource.metadata.ragOnly){
        return 0;
    }
    return 1000;
}

export const getAvailableAssistantsForDataSources = (model, dataSources, assistants = defaultAssistants) => {
    console.log("getAvailableAssistantsForDataSources function")

    // if (!dataSources || dataSources.length === 0) {
    //     return [defaultAssistant];
    // }

    return assistants.filter((assistant) => {
        return assistant.handlesDataSources(dataSources) && assistant.handlesModel(model);
    });
}


export const chooseAssistantForRequest = async (llm, model, body, dataSources, assistants = defaultAssistants) => {
    logger.info(`Choose Assistant for Request `);

    const clientSelectedAssistant = body.options?.assistantId ?? null;

    let selectedAssistant = null;
    if (clientSelectedAssistant) {
        logger.info(`Client Selected Assistant: `, clientSelectedAssistant);
        // For group ast
        const user = llm.params.account.user;
        const token = llm.params.account.accessToken;
        
        selectedAssistant = await getUserDefinedAssistant(user, defaultAssistant, clientSelectedAssistant, token);
        if (!selectedAssistant) {
            llm.sendStatus(newStatus(
                {   inProgress: false,
                    message: "Selected Assistant Not Found",
                    icon: "assistant",
                    sticky: true
                }));
            llm.forceFlush();

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
        const status = newStatus({inProgress: true, message: "Choosing an assistant to help..."});
        llm.sendStatus(status);
        llm.forceFlush();

        // Look for any body.messages.data.state.currentAssistant going in reverse order through the messages
        // and choose the first one that is found.
        const currentAssistant = body.messages.map((m) => {
            return (m.data && m.data.state && m.data.state.currentAssistant) ? m.data.state.currentAssistant : null;
        }).reverse().find((a) => a !== null);

        // Hack to make AWS lambda send the status update and not buffer
        let availableAssistants = getAvailableAssistantsForDataSources(model, dataSources, assistants);

        if (availableAssistants.some((a) => a.name === currentAssistant) &&
            (!dataSources || dataSources.length === 0)) {
            // Future, we can automatically default to the last used assistant to speed things
            // up unless some predetermined condition is met.
            availableAssistants = [assistants.find((a) => a.name === currentAssistant)]
        }

        const start = new Date().getTime();
        const selectedAssistantName = (availableAssistants.length > 1 ) ?
            await chooseAssistantForRequestWithLLM(llm, body, dataSources,
                availableAssistants) : availableAssistants[0].name;
        const timeToChoose = new Date().getTime() - start;
        logger.info(`Selected assistant ${selectedAssistantName}`);
        logger.info(`Time to choose assistant: ${timeToChoose}ms`);

        selectedAssistant = assistants.find((a) => a.name === selectedAssistantName);

        status.inProgress = false;
        llm.sendStatus(status);
    }

    const selected = selectedAssistant || defaultAssistant;

    logger.info("Sending State Event to Stream ", selectedAssistant.name);
    let stateInfo = {
        currentAssistant: selectedAssistant.name,
        currentAssistantId: clientSelectedAssistant || selectedAssistant.name,
    }
    if (selectedAssistant.disclaimer) stateInfo = {...stateInfo, currentAssistantDisclaimer : selectedAssistant.disclaimer};
    
    llm.sendStateEventToStream(stateInfo);

    llm.sendStatus(newStatus(
        {
            inProgress: false,
            message: "The \"" + selected.displayName + " Assistant\" is responding.",
            icon: "assistant",
            sticky: true
        }));
    llm.forceFlush();

    return selected;
}
