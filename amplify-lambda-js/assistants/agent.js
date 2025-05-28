import { DynamoDBClient, QueryCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import axios from "axios";


export const getLatestAgentState = async function(accessToken, currentUser, sessionId) {
    const dynamodb = new DynamoDBClient({});

    const params = {
        TableName: process.env.AGENT_STATE_DYNAMODB_TABLE,
        KeyConditionExpression: '#user = :user AND #sessionId = :sessionId',
        ExpressionAttributeNames: {
            '#user': 'user',
            '#sessionId': 'sessionId'
        },
        ExpressionAttributeValues: {
            ':user': { S: currentUser },
            ':sessionId': { S: sessionId }
        },
        ScanIndexForward: false,
        Limit: 1
    };

    const response = await dynamodb.send(new QueryCommand(params));
    return response.Items?.[0] ? unmarshall(response.Items[0]) : null;
}

export const listenForAgentUpdates = async function(accessToken, currentUser, sessionId, onAgentStateUpdate) {
    let errorsRemaining = 15;
    while (true) {
        try {
            const state = await getLatestAgentState(accessToken, currentUser, sessionId);
            const shouldContinue = onAgentStateUpdate(state);
            if (!shouldContinue) {
                break;
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
        } catch (error) {
            // It is possible the state has not been written yet, so we will retry a few times
            console.error("Error checking agent state:", error);
            if (errorsRemaining <= 0) {
                break;
            }
            else {
                errorsRemaining--;
                await new Promise(resolve => setTimeout(resolve, 2000));
            }
        }
    }
}



export const invokeAgent = async function(accessToken, sessionId, requestId, prompt, metadata={}) {
    // Do other async operations here if needed
    // const someData = await fetchSomeData();
    const endpoint = process.env.AGENT_ENDPOINT;

    console.log("Invoking agent with sessionId:", sessionId);

    const response = await axios.post(
        endpoint,
        {
            data: {
                sessionId,
                requestId,
                prompt,
                metadata
            }
        },
        {
            headers: {
                Authorization: "Bearer "+accessToken
            }
        }
    );

    return response.data;
}
