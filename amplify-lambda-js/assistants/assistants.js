//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {newStatus} from "../common/status.js";
import {csvAssistant} from "./csv.js";
import {getLogger} from "../common/logging.js";
import {getChatFn, getCheapestModel} from "../common/params.js";
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

        if(params.blockTerminator) {
            body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
        }

        if(!body.options.ragOnly && (dataSources.length > 1 || aboveLimit)){
            return mapReduceAssistant.handler(llm, params, body, dataSources, responseStream);
        }
        else {
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
                getCheapestModel(params),
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
            return model.id.includes("gpt") ? totalTokens.gpt : 
                   model.id.includes("anthropic") ? totalTokens.claude : "";
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

const isUserDefinedAssistant = (assistantId) => {
    return assistantId && assistantId !== "default" && assistantId.startsWith("astp/");
}

export const chooseAssistantForRequest = async (llm, model, body, dataSources, assistants = defaultAssistants) => {
    logger.info(`Choose Assistant for Request `);

    const clientSelectedAssistant = body.options?.assistantId ?? null;

    const lastMessage = body.messages.slice(-1)[0];
    const hasTools = lastMessage.configuredTools && lastMessage.configuredTools.length > 0;

    let selectedAssistant = null;
    if(clientSelectedAssistant) {
        logger.info(`Client Selected Assistant: `, clientSelectedAssistant);
        // For group ast
        const user = llm.params.account.user;
        const ast_owner = clientSelectedAssistant.startsWith("astgp") ? body.options.groupId : user;
        selectedAssistant = await getUserDefinedAssistant(user, defaultAssistant, ast_owner, clientSelectedAssistant);
        if (!selectedAssistant) {
            llm.sendStatus(newStatus(
                {   inProgress: false,
                    message: "Selected Assistant Not Found",
                    icon: "assistant",
                    sticky: true
                }));
            llm.forceFlush();
        }
    }
    else if(hasTools) {
        logger.info("Using tools");
        // For group ast
        const user = llm.params.account.user;

        const tools = lastMessage.configuredTools;

        const assistantOps = tools.map((tool) => {

            // Filter out empty parameters that are not needed and will
            // be assigned by AI
            const filteredParams = Object.fromEntries(
                Object.entries(tool.parameters || {}).filter(([_, val]) => val.value !== "")
            );

            let customName = tool.operation.name;
            if(tool.customName && tool.customName.length > 0) {
                customName = tool.customName;
            }

            return {
                ...tool.operation,
                name: tool.operation.name,
                customName: customName,
                bindings: filteredParams,
            }
        });

        selectedAssistant = fillInAssistant(
            {
                name: "Amplify Automation",
                instructions: `
You are an advanced AI assistant with access to specialized functions that enable you to perform tasks beyond conversation. Your primary goal is to help users accomplish their objectives by thoughtfully utilizing these functions when appropriate.

## Core Principles

1. DELIBERATE DECISION-MAKING
   - Stop and think step by step before deciding on an approach
   - Consider multiple solution paths and select the most appropriate one
   - Explicitly reason through trade-offs between different approaches
   - When uncertain, gather more information before proceeding

2. FUNCTION USAGE GUIDELINES
   - Only use functions when necessary to accomplish the user's goal
   - Select the most appropriate function for each task
   - Structure function calls with precise parameters
   - Validate inputs before making function calls
   - Handle errors gracefully and attempt reasonable fallbacks

3. PROBLEM-SOLVING FRAMEWORK
   - Understand: Clarify the user's objective completely before acting
   - Plan: Outline a clear strategy, including which functions to use and in what sequence
   - Execute: Implement the plan methodically, documenting each step
   - Verify: Confirm results match expectations
   - Refine: If outcomes are suboptimal, adjust your approach and try again

4. COMMUNICATION PROTOCOL
   - Explain your reasoning before making function calls
   - Provide clear summaries of function results
   - When presenting multiple options, justify your recommendations
   - Use appropriate technical detail based on user expertise

5. TOOL RESPONSIBILITY
   - Respect rate limits and resource constraints
   - Prioritize user data privacy and security
   - Use the minimal set of functions needed to accomplish the task
   - Acknowledge limitations of available functions

## Function Usage Patterns

For each function type, follow these specialized protocols:

### Data Retrieval Functions
- Formulate precise queries to minimize irrelevant results
- Request only necessary data to respect privacy and efficiency
- Parse and filter responses before presenting to user

### Computation Functions
- Validate inputs prior to execution
- Structure complex calculations as smaller, verifiable steps
- Include appropriate error handling

### External API Functions
- Format requests according to API documentation
- Implement appropriate authentication
- Handle potential network or service failures gracefully

### File Operation Functions
- Confirm operations on sensitive data
- Validate file formats before processing
- Implement safeguards against unintended data loss

## Decision Framework
When deciding whether to use functions, evaluate:
1. Is the task impossible to complete conversationally?
2. Would using a function significantly improve accuracy or efficiency?
3. Does the user's request implicitly require function capabilities?
4. Have simpler approaches been exhausted?

## Function Call Format
When invoking functions:
1. Use the correct syntax specific to your environment
2. Include all required parameters
3. Format parameter values appropriately
4. Add helpful comments when complexity warrants explanation

## Error Handling Protocol

If you encounter multiple consecutive errors (two or more) while using the same tool or function:

1. Stop immediately and do not make further attempts with that particular tool
2. Clearly explain to the user:
   - That you've encountered repeated failures with the specific tool
   - A brief, non-technical summary of what you were attempting to accomplish
   - That you're halting further attempts to prevent wasting time or resources

3. Offer alternative approaches when possible:
   - Suggest a different tool or method that might accomplish the same goal
   - Propose breaking down the task into smaller components that might be more manageable
   - Ask if the user has additional information that could help overcome the obstacle

4. Request clear guidance on how to proceed rather than continuing to attempt the same failed approach

Remember that respecting the user's time and providing transparency about limitations is more valuable than persisting with unsuccessful approaches.


Remember that your goal is to augment your capabilities through judicious use of functions, not to rely on them when simpler approaches would suffice. Always prioritize user needs and clear communication.
                `,
                description: "Amplify Automation",
                data: {
                    opsLanguageVersion: "v4",
                    operations: assistantOps
                }
            },
            defaultAssistant
        )
    }
    else if (body.options.codeInterpreterOnly && (!body.options.api_accessed)) {
        selectedAssistant = await codeInterpreterAssistant(defaultAssistant);
        //codeInterpreterAssistant;
    } else if (body.options.artifactsMode && (!body.options.api_accessed)) {
        selectedAssistant = ArtifactModeAssistant;
        console.log("ARTIFACT MODE DETERMINED")
    }

    
    if(selectedAssistant === null) {
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