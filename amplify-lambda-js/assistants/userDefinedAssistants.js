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

                    if(assistant.data.dataSourceOptions.insertConversationDocumentsMetadata){
                        dataSourceMetadataForInsertion.push(...(assistant.dataSources || []));
                        dataSourceMetadataForInsertion.push(...(available.conversationDataSources || []));
                    }
                    if(assistant.data.dataSourceOptions.insertAttachedDocumentsMetadata){
                        dataSourceMetadataForInsertion.push(...(available.attachedDataSources || []));
                    }

                if(dataSourceMetadataForInsertion.length > 0) {

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
                }

            if (body.options.addMsgContent) {
                body.messages[body.messages.length - 1].content += body.options.addMsgContent;
            }

            const messagesWithoutSystem = body.messages.filter(
                (message) => message.role !== "system"
            );

            const groupType = body.options.groupType;
            if (groupType) {
                const groupTypeData = assistant.data.groupTypeData[groupType];
                assistant.instructions += "\n\n" + groupTypeData.additionalInstructions
                assistant.dataSources = [...assistant.dataSources, ...groupTypeData.dataSources]
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