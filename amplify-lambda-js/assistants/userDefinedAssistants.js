
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {getDataSourcesByUse, isImage} from "../datasource/datasources.js";
import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import {getOps} from "./ops/ops.js";
import {fillInTemplate} from "./instructions/templating.js";
import {PutObjectCommand, S3Client} from "@aws-sdk/client-s3";
import {addAllReferences, DATASOURCE_TYPE, getReferences, getReferencesByType} from "./instructions/references.js";
import {opsLanguages} from "./opsLanguages.js";
import {newStatus, getThinkingMessage} from "../common/status.js";
import {invokeAgent, getLatestAgentState, listenForAgentUpdates} from "./agent.js";

const s3Client = new S3Client();
const dynamodbClient = new DynamoDBClient({ });

async function getAssistantByAlias(user, assistantId) {
    const params = {
        TableName: process.env.ASSISTANTS_ALIASES_DYNAMODB_TABLE,
        Key: {
            user: { S: user },
            assistantId: { S: assistantId +"?type=latest" }
        }
    };

    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        if (response.Item) {
            return unmarshall(response.Item);
        } else {
            console.log("No item retrieved in getAssistantByAlias")
            return null;
        }
    } catch (error) {
        console.error('Error getting assistant alias:', error);
        return null;
    }
}

async function getAssistantByAssistantDatabaseId(id) {

    const params = {
        TableName: process.env.ASSISTANTS_DYNAMODB_TABLE,
        Key: {
            id: { S: id }
        }
    };

    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        if (response.Item) {
            return unmarshall(response.Item);
        } else {
            console.log("No item retrieved in getAssistantByAssistantDatabaseId")
            return null;
        }
    } catch (error) {
        console.error('Error getting assistant alias:', error);
        return null;
    }
}

const saveChatToS3 = async (assistant, currentUser, chatBody, metadata) => {
    console.log("saveChatToS3 function")
    // Define the parameters for the putObject operation

    if(!process.env.ASSISTANT_LOGS_BUCKET_NAME) {
        console.error("ASSISTANT_LOGS_BUCKET_NAME environment variable is not set");
        console.log("Will not log assistant chat");
        return null;
    }

    // date string in format 2023-12-29/
    const date = new Date().toISOString().split('T')[0];
    const requestId = chatBody.options.requestId;

    const key = `${assistant.assistantId}/${date}/${currentUser}/${requestId}.json`;

    const putObjectParams = {
        Bucket: process.env.ASSISTANT_LOGS_BUCKET_NAME,
        Key: key,
        Body: JSON.stringify({request:chatBody, currentUser, metadata}),
    };

    try {
        // Upload the object to S3
        const data = await s3Client.send(new PutObjectCommand(putObjectParams));
        console.log("Object uploaded successfully. Location:", resultKey);
        return data;
    } catch (error) {
        console.error("Error uploading object:", error);
        throw error;
    }
}


export const getUserDefinedAssistant = async (current_user, assistantBase, ast_owner, assistantPublicId) => {
    if (!ast_owner) return null;

    // verify the user has access to the group since this is a group assistant
    if (assistantPublicId.startsWith("astgp") && current_user !== ast_owner) {
        console.log( `Checking if ${current_user} is a member of group: ${ast_owner}`);

        try {
            const params = {
                TableName: process.env.GROUPS_DYNAMO_TABLE,
                Key: {
                    group_id: { S: ast_owner }
                }
            };
        
            const response = await dynamodbClient.send(new GetItemCommand(params));
            
            if (response.Item) {
                const item = unmarshall(response.Item);
                // Check if the group is public or if the user is in the members
                if (!(item.isPublic || 
                    (item.members && Object.keys(item.members).includes(current_user)) || 
                    (item.systemUsers && item.systemUsers.includes(current_user)))) {
                    console.error( `User is not a member of groupId: ${ast_owner}`);
                    return null;
                }
            } else {
                console.error(`No group entry found for groupId: ${ast_owner}`);
                return null;
            }
        
        } catch (error) {
            console.error(`An error occurred while processing groupId ${ast_owner}:`, error);
            return null;
        }
    }

    const assistantAlias = await getAssistantByAlias(ast_owner, assistantPublicId);

    if (assistantAlias) {
        const assistant = await getAssistantByAssistantDatabaseId(
            assistantAlias.data.id
        );

        console.log("Assistant found by alias: ", assistant);

        const userDefinedAssistant =  fillInAssistant(assistant, assistantBase)
        console.log(`Client Selected Assistant: `, userDefinedAssistant.displayName)
        return userDefinedAssistant;
    }

    return null;
};


export const fillInAssistant = (assistant, assistantBase) => {
    return {
        name: assistant.name,
        displayName: assistant.name,
        handlesDataSources: (ds) => {
            return true;
        },
        handlesModel: (model) => {
            return true;
        },
        description: assistant.description,

        disclaimer: assistant.disclaimer ?? '',

        handler: async (llm, params, body, ds, responseStream) => {

                const references = {};

            if(assistant.skipRag) {
                params = {
                    ...params,
                options:{...params.options, skipRag: true}
                }
            }

            if(assistant.ragOnly) {
                params = {
                    ...params,
                    options:{...params.options, ragOnly: true}
                }
            }

            const dataSourceOptions = {};
            if(assistant.data && assistant.data.dataSourceOptions) {
                dataSourceOptions.dataSourceOptions = assistant.data.dataSourceOptions;
            }

            const suffixMessages = [];
            const extraMessages = [];

            if(params && params.options){
                if(params.options.timeZone) {
                    extraMessages.push({
                        role: "user",
                        content: "Helpful info, don't repeat: The user is in the " + params.options.timeZone + " time zone."
                    });
                }
                if(params.options.time){
                    extraMessages.push({
                        role: "user",
                        content: "Helpful info, don't repeat: The current time for the user is " + params.options.time
                    });
                }
            }

            if(assistant.data && assistant.data.messageOptions) {
                if(assistant.data.messageOptions.includeMessageIds){

                    const messageIdMapping = {};

                    body.messages = body.messages.map((m, i) => {

                        messageIdMapping[i] = m.id;

                        return {
                            ...m,
                            content: "MsgID: " + i + "\n\n" + m.content
                        };
                    });

                    llm.sendStateEventToStream({
                       messageIdMapping
                    });

                    extraMessages.push({
                        role: "user",
                        content:"You can have references or prior messages inserted into your response by " +
                            "referencing the MsgId like this %^MsgID. Examples %^0, %^1, etc. The reference" +
                            "will be replaced with the content of that message. DO NOT OUTPUT OR TALK ABOUT " +
                            "THESE IDS TO THE USER."
                    });
                }
            }


            if(assistant.data && assistant.data.dataSourceOptions) {

                const dataSourceMetadataForInsertion = [];
                const available = await getDataSourcesByUse(params, body, ds);

                if (assistant.data.dataSourceOptions.insertConversationDocumentsMetadata) {
                        dataSourceMetadataForInsertion.push(...(assistant.dataSources || []));
                        dataSourceMetadataForInsertion.push(...(available.conversationDataSources || []));
                }
                if (assistant.data.dataSourceOptions.insertAttachedDocumentsMetadata ){
                    dataSourceMetadataForInsertion.push(...(available.attachedDataSources || []));
                }

                if (dataSourceMetadataForInsertion.length > 0) {

                        const dataSourceSummaries = dataSourceMetadataForInsertion.map(ds => {

                            // If we have a userDataSourceId, use that, otherwise use the id.
                            // This is important if we need the original file text for any reason.
                            // The hash data source id can't be used to get the file text, but the
                            // user data source id can. The user data source id can also be translated
                            // back to the hash data source id if needed.
                            const dsid =  (ds.metadata && ds.metadata.userDataSourceId) ?
                                ds.metadata.userDataSourceId : ds.id;

                            return {id: dsid, name: ds.name, type:ds.type, metadata:(ds.metadata || {})};
                        });

                        addAllReferences(references, DATASOURCE_TYPE, dataSourceSummaries);
                        const dsR = getReferencesByType(references, DATASOURCE_TYPE);

                        const metadataStr = (r) => {
                            return Object.entries(r.object.metadata).map(
                                ([k,v]) => `${k}:${v}`).join("; ");
                        }

                        const dataSourceText = "Short_ID,NAME,TYPE,METADATA\n" + dsR.map(
                            r => r.type+r.id +","+r.object.name+","+r.object.type+","+metadataStr(r)).join("\n");

                        extraMessages.push({
                            role: "user",
                            content:
                            `You have the following data sources and documents available:
                            -------------                        
                            ${dataSourceText}
                            -------------
                            Any operation that asks for an ID or Key should be supplied with the Short_ID from the list above
                            of the corresponding data source or document. Avoid discussing these IDs, keys, etc. with the user
                            as they can't see them. If they ask you about them, it is OK to tell them and use them for operations, 
                            but otherwise don't describe them in your answers as it might confuse the user.
                            `,
                        });
                }
                if (assistant.data.dataSourceOptions.includeDownloadLinks ) { 
                    extraMessages.push({
                        role: "user",
                        content: `
                        Any documents you reference in your response MUST be formatted in the following way:
                            [<filename>](#dataSource:<filename>)

                            - Any spaces in the file name must be converted to '&' in the (#dataSource:<filename>)
                            Examples:
                            - Original Filename: Project Plan 2023.docx
                              Formatted: [Project Plan 2023.docx](#dataSource:Project&Plan&2023.docx)

                            - Original Filename: monthly_report.pdf
                              Formatted: [monthly_report.pdf)](#dataSource:monthly_report.pdf)
                        
                        Never under any circumstance make up document names. If you are not made aware of any documents then assume you do not have any to reference.
                        `});
                }
            }

            let blockTerminator = null;

            if(assistant.data && assistant.data.opsLanguageVersion === "v4") {

                const statusInfo = newStatus(
                    {
                        animated: true,
                        inProgress: true,
                        sticky: true,
                        summary: `Thinking...`,
                        icon: "info",
                    }
                );

                const response = invokeAgent(
                    params.account.accessToken,
                    params.options.conversationId,
                    body.messages,
                    {assistant}
                );
                llm.sendStatus(statusInfo);
                llm.forceFlush();
                llm.forceFlush();

                //const result = await response;
                var stopPolling = false;
                var result = null;
                await Promise.race([
                    response.then(r => {
                        stopPolling = true;
                        result = r;
                        return r;
                    }),
                    listenForAgentUpdates(params.account.accessToken, params.account.user, params.options.conversationId, (state) => {

                        if(!state) {
                            return !stopPolling;
                        }

                        console.log("Agent state updated:", state);
                        let msg = getThinkingMessage();
                        let details = "";//JSON.stringify(state);
                        if(state.state){
                            try {
                                const tool_call = JSON.parse(state.state);
                                const tool = tool_call.tool;
                                if(tool === "terminate"){
                                    msg = "Hold on..."
                                }
                                else if(tool === "exec_code"){
                                    msg = "Executing code..."
                                    details = `\`\`\`python\n\n${tool_call.args.code}\n\n\`\`\``;
                                }
                                else {
                                    function formatToolCall(toolCall) {
                                        const lines = [`Calling: ${toolCall.tool}`, '   with:'];
                                        Object.entries(toolCall.args).forEach(([key, value]) => {
                                            lines.push(`      ${key}: ${JSON.stringify(value)}`);
                                        });
                                        return lines.join('\n');
                                    }

                                    msg = "Calling: " + tool_call.tool;
                                    details = formatToolCall(tool_call);
                                }
                            }catch (e){
                            }
                        }
                        else {
                            msg = `Agent state updated: ${JSON.stringify(state)}`;
                        }
                        statusInfo.summary = msg;
                        statusInfo.message = details
                        llm.sendStatus(statusInfo);
                        llm.forceFlush();
                        return !stopPolling;
                    })
                ]);

                llm.sendStateEventToStream({
                    agentLog: result
                })
                llm.forceFlush();

                if (result.success) {
                    let responseFromAssistant = result.data.result.findLast(msg => msg.role === 'assistant').content;

                    if(responseFromAssistant.args && responseFromAssistant.args.message){
                        responseFromAssistant = responseFromAssistant.args.message;
                    }
                    else {
                        responseFromAssistant = JSON.stringify(responseFromAssistant);
                    }

                    const summaryRequest = {
                        ...body,
                        messages: [
                            {
                                role: "user",
                                content:
                                    `The user's prompt was: ${body.messages.slice(-1)[0].content}` +
                                    `\n\nA log of the assistant's reasoning / work:\n---------------------\n${JSON.stringify(result.data.result)}` +
                                    `\n\n---------------------` +
                                    `\n\nRespond to the user.`
                            }]};

                    await llm.prompt(summaryRequest, []);

                }

                return;

            }
            else if(assistant.data && assistant.data.operations && assistant.data.operations.length > 0 && assistant.data.opsLanguageVersion !== "custom") {

                const opsLanguageVersion = assistant.data.opsLanguageVersion || "v1";
                const langVersion = opsLanguages[opsLanguageVersion];
                const instructionsPreProcessor = langVersion.instructionsPreProcessor;

                if (instructionsPreProcessor) {
                    assistant.instructions = instructionsPreProcessor(assistant.instructions);
                }

                const langMessages = langVersion.messages;
                blockTerminator = langVersion.blockTerminator;
                extraMessages.push(...langMessages);
                suffixMessages.push(...(langVersion.suffixMessages || []));

            }


            const messagesWithoutSystem = body.messages.filter(
                (message) => message.role !== "system"
            );

            const groupType = body.options.groupType;
            if (groupType) {
                const groupTypeData = assistant.data.groupTypeData[groupType];
                if (!groupTypeData.isDisabled) {
                    assistant.instructions += "\n\n" + groupTypeData.additionalInstructions;
                    assistant.dataSources = [...assistant.dataSources, ...groupTypeData.dataSources];
                }
            }

            if (assistant.data && assistant.data.supportConvAnalysis) {
                body.options.analysisCategories = assistant.data?.analysisCategories ?? [];
            }

            const instructions = await fillInTemplate(
                llm,
                params,
                body,
                ds,
                assistant.instructions,
                {
                    assistant: assistant,
                    operations: assistant.data?.operations || []
                }
            );

                llm.sendStateEventToStream({
                    references: getReferences(references),
                    opsConfig: {
                        opFormat: {
                            name: "functionCalling",
                            type: "regex",
                            opPattern: "(\\w+)\\s*\\((.*)\\)\\s*$",
                            opIdGroup: 1,
                            opArgsGroup: 2,
                        },
                    }
                })

                const updatedBody = {
                    ...body,
                    messages: [
                        ...messagesWithoutSystem.slice(0,-1),
                        {
                            role: "user",
                            content: "Pay close attention to any provided information. Unless told otherwise, " +
                                "cite the information you are provided with quotations supporting your analysis " +
                                "the [Page X, Slide Y, Paragraph Q, etc.] of the quotation.",
                            data: {dataSources: assistant.dataSources}
                        },
                        {
                            role: 'system',
                            content: instructions,
                        },
                        ...extraMessages,
                        body.messages.slice(-1)[0],
                        ...suffixMessages
                    ],
                    options: {
                        ...body.options,
                        ...dataSourceOptions,
                        prompt: instructions,
                    }
                };
            
            // for now we will include the ds in the current message
            if (assistant.dataSources && !dataSourceOptions.disableDataSources) updatedBody.imageSources =  [...(updatedBody.imageSources || []), ...assistant.dataSources.filter(ds => isImage(ds))];

            await assistantBase.handler(
                llm,
                {...params, blockTerminator: blockTerminator || params.blockTerminator},
                updatedBody,
                ds,
                responseStream);

            try {
                if (assistant.data && assistant.data.logChats) {
                    const user = assistant.data.logAnonymously ?
                        "anonymous" : params.account.user;

                    await saveChatToS3(
                        assistant,
                        user,
                        body,
                        {
                            user: params.account.user,
                            assistant: assistant,
                            dataSources: ds,
                        }
                    );
                }
            } catch (e) {
                console.error('Error logging assistant chat to S3:', e);
            }
        }
    };

}