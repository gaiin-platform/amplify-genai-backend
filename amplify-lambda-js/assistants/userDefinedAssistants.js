import {ModelID, Models} from "../models/models.js";
import {getDataSourcesByUse} from "../datasource/datasources.js";
import {mapReduceAssistant} from "./mapReduceAssistant.js";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import {getOps} from "./ops/ops.js";
import {fillInTemplate} from "./instructions/templating.js";

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
            return null;
        }
    } catch (error) {
        console.error('Error getting assistant alias:', error);
        return null;
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

                assistantBase.handler(
                    llm,
                    params,
                    updatedBody,
                    ds,
                    responseStream);
            }
        };

        return userDefinedAssistant;
    }

    return null;
};