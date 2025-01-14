//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {SQSClient, SendMessageCommand} from '@aws-sdk/client-sqs';
import { v4 as uuidv4 } from 'uuid';


// Initialise the SQS client
const sqsClient = new SQSClient();


export const createChatTask = (accessToken, user, resultKey, chatBody, cheapestModel, options = {}, ) => {

    const task =
        {
            op: "chat",
            user,
            accessToken,
            resultKey,
            params: {
                "user": user,
                "body": {
                    "model": cheapestModel.id,
                    "temperature": 1,
                    "max_tokens": 1000,
                    "stream": true,
                    "dataSources": [],
                    ...chatBody,
                    "options": {
                        "requestId": uuidv4(),
                        "model": cheapestModel,
                        "key": "",
                        ...options
                    }
                },
                "accessToken": accessToken
            }
        };

    return task;
}

export const sendAssistantTaskToQueue = async (task) => {
    const queueUrl = process.env.assistant_task_queue_url; // Make sure this env variable is set
    const params = {
        QueueUrl: queueUrl,
        MessageBody: JSON.stringify(task), // Convert the payload to a JSON string
    };

    try {
        const data = await sqsClient.send(new SendMessageCommand(params));
        console.log("Success", data);
        return data;
    } catch (error) {
        console.error("Error", error);
        throw error;
    }
}
