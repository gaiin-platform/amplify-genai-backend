
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {ModelID, Models} from "../models/models.js";
import {getDataSourcesByUse, isImage} from "../datasource/datasources.js";
import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import {getOps} from "./ops/ops.js";
import {fillInTemplate} from "./instructions/templating.js";
import {PutObjectCommand, S3Client} from "@aws-sdk/client-s3";
import {addAllReferences, DATASOURCE_TYPE, getReferences, getReferencesByType} from "./instructions/references.js";

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


export const getUserDefinedAssistant = async (assistantBase, user, assistantPublicId) => {

    const assistantAlias = await getAssistantByAlias(user, assistantPublicId);

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

            const extraMessages = [];

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

            if (assistant.data && assistant.data.apiOptions) {
                if (assistant.data.apiCapabilities) {
                    // console.log("Api Capabilities", assistant.data.apiCapabilities);
                    let assistantApis = '';
                    for (let i = 0; i < assistant.data.apiCapabilities.length; i++) {
                        assistantApis += '---------------\n';
                        const capability = assistant.data.apiCapabilities[i];

                        assistantApis += `Description: ${capability.Description}\n`;
                        assistantApis += `URL: ${capability.URL}\n`;
                        assistantApis += `RequestType: ${capability.RequestType}\n`;

                        assistantApis += 'Auth:\n';
                        for (const [key, value] of Object.entries(capability.Auth)) {
                            assistantApis += `  ${key}: ${value}\n`;
                        }

                        assistantApis += 'Body:\n';
                        for (const [key, value] of Object.entries(capability.Body)) {
                            assistantApis += `  ${key}: ${value}\n`;
                        }

                        assistantApis += 'Headers:\n';
                        for (const [key, value] of Object.entries(capability.Headers)) {
                            assistantApis += `  ${key}: ${value}\n`;
                        }

                        assistantApis += 'Parameters:\n';
                        for (const [key, value] of Object.entries(capability.Parameters)) {
                            assistantApis += `  ${key}: ${value}\n`;
                        }
                        assistantApis += '---------------\n';
                    }
                    // console.log("Assistant APIs:", assistantApis);
                    const customAutoBlockTemplate = `\`\`\`customAuto
{
    "RequestType": "HTTP_METHOD",
    "URL": "https://api.example.com/v1/endpoint",
    "Parameters": {
        "key1": "value1",
        "key2": "value2"
    },
    "Body": {
        "property1": "value1",
        "property2": "value2"
    },
    "Headers": {
        "Custom-Header": "value"
    },
    "Auth": {
        "type": "bearer",
        "token": "your_auth_token"
    }
}
\`\`\``;
                    // console.log("Custom Auto Block:", customAutoBlockTemplate);
                    const msgtest = `You have access to the following APIs (assume any empty field is not relevant):
${assistantApis}
If the user prompts you in a way that is relevant to one of the APIs you have access to, create a customAuto block within your answer as described here:
1. RequestType: The HTTP method (GET, POST, PUT, DELETE, etc.)
2. URL: The complete endpoint URL, including API version if applicable
3. Parameters: Query parameters, if applicable (as a nested object)
4. Body: Request body, if applicable (as a nested object for POST/PUT requests)
5. Headers: Any custom headers required (as a nested object)
6. Auth: Authentication details if required (as a nested object)
Format your response as follows:
${customAutoBlockTemplate}
Use empty objects {} for Parameters, Body, Headers, or Auth if not applicable.
I will take the customAuto block you created, execute it, and provide it back to you.
Incorporate the response of the customAuto block in your response to the user.
If the response is an error, inform the user and ask if they want to try again. If they say yes, analyze the error to determine what's wrong with the API request, update it, and create an updated customAuto block.
If the response is successful, inform the user with the relevant information.`;
                    console.log(msgtest);
                    extraMessages.push({
                        role: "user",
                        content: `
                        You have access to the following APIs (assume any empty field is not relevant):
                        ${assistantApis}
                        If the user prompts you in a way that is relevant to one of the APIs you have access to, create a customAuto block within your answer as described here:
                        1. RequestType: The HTTP method (GET, POST, PUT, DELETE, etc.)
                        2. URL: The complete endpoint URL, including API version if applicable
                        3. Parameters: Query parameters, if applicable (as a nested object)
                        4. Body: Request body, if applicable (as a nested object for POST/PUT requests)
                        5. Headers: Any custom headers required (as a nested object)
                        6. Auth: Authentication details if required (as a nested object)
                        Format your response as follows:
                        ${customAutoBlockTemplate}
                        Use empty objects {} for Parameters, Body, Headers, or Auth if not applicable.
                        I will take the customAuto block you created, execute it, and provide it back to you.
                        Incorporate the response of the customAuto block in your response to the user.
                        If the response is an error, inform the user and ask if they want to try again. If they say yes, analyze the error to determine what's wrong with the API request, update it, and create an updated customAuto block.
                        If the response is successful, inform the user with the relevant information.
                        `});
                }
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

            const instructions = await fillInTemplate(
                llm,
                params,
                body,
                ds,
                assistant.instructions,
                {
                    assistant: assistant,
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
                        body.messages.slice(-1)[0]
                    ],
                    options: {
                        ...body.options,
                        ...dataSourceOptions,
                        prompt: instructions,
                    }
                };
            
            // for now we will include the ds in the current message
            if (assistant.dataSources) updatedBody.imageSources =  [...(updatedBody.imageSources || []), ...assistant.dataSources.filter(ds => isImage(ds))];

            await assistantBase.handler(
                llm,
                params,
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