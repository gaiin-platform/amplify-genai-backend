import {ModelID, Models} from "../models/models.js";
import {getDataSourcesByUse} from "../datasource/datasources.js";
import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import {getOps} from "./ops/ops.js";
import {fillInTemplate} from "./instructions/templating.js";
import {PutObjectCommand, S3Client} from "@aws-sdk/client-s3";

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

        const userDefinedAssistant = {
            name: assistant.name,
            displayName: assistant.name,
            handlesDataSources: (ds) => {
                return true;
            },
            handlesModel: (model) => {
                return true;
            },
            description: assistant.description,

            handler: async (llm, params, body, ds, responseStream) => {


                if(assistant.skipRag) {
                    params = {
                        ...params,
                    options:{...params.options, skipRag: true}
                    }
                }

                const messagesWithoutSystem = body.messages.filter(
                    (message) => message.role !== "system"
                );

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
                        body.messages.slice(-1)[0]
                    ],
                    options: {
                        ...body.options,
                        prompt: instructions,
                    }
                };

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
        console.log(`Client Selected Assistant: `, userDefinedAssistant.displayName)
        return userDefinedAssistant;
    }

    return null;
};