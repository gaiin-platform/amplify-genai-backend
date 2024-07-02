import {DynamoDBClient, QueryCommand, GetItemCommand, PutItemCommand, DeleteItemCommand,ScanCommand, UpdateItemCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import { CognitoJwtVerifier } from "aws-jwt-verify";
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// Since __dirname is not available in ES module scope, you have to construct the path differently.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Now, use the constructed path to point to your .env.local file
config({ path: join(__dirname, '../../.env.local') });


// Read environment variables
const userPoolId = process.env.COGNITO_USER_POOL_ID;
const clientId = process.env.COGNITO_CLIENT_ID;

// Ensure the environment variables are defined
if (!userPoolId || !clientId) {
    throw new Error("Environment variables USER_POOL_ID and CLIENT_ID must be defined");
}

const verifier = CognitoJwtVerifier.create({
    userPoolId: userPoolId,
    tokenUse: "access",
    clientId: clientId,
});

export const extractParams = async (event) => {

        // Extract the Authorization header
        const authorizationHeader = event.headers.Authorization || event.headers.authorization;
        
        if (!authorizationHeader) {
            return {
                statusCode: 401,
                body: JSON.stringify({message: "Unauthorized"}),
            };
        }

       

        // Extract the token part from the Authorization header
        const match = authorizationHeader.match(/^Bearer (.+)$/i);

        if (!match) {
            return {
                statusCode: 401,
                body: JSON.stringify({message: "Unauthorized: Bearer token not found"}),
            };
        }

        const token = match[1];


        ////// api path  if token prefix amp- ////// 
        if (token.startsWith("amp-")) return api_authenticator(token, event);
        // if (true) return api_authenticator("amp-ea9b082c-247e-4692-969f-52a10d611528", event);



        let payload = null;
        try {
            payload = await verifier.verify(
                token // the JWT as string
            );

        } catch (e) {
            console.error(e);

            return {
                statusCode: 401,
                body: JSON.stringify({message: "Unauthorized. Invalid token."}),
            };
        }

        const current_user = payload.username.slice('vupingidp_'.length);
        console.log("Current user: "+current_user)

        let requestBody;
        try {
            requestBody = JSON.parse(event.body);

            return {user: current_user, body: requestBody, accessToken: token};
        } catch (e) {
            // If error occurs during parsing, return an appropriate response
            return {
                statusCode: 400,
                body: JSON.stringify({ message: "Invalid JSON in request body" }),
            };
        }
}

const api_authenticator = async (apiKey, event) => {
    const apiTable = process.env.API_KEYS_DYNAMODB_TABLE;
    const dynamodbClient = new DynamoDBClient({});

    if (!apiTable) {
        console.log("API_KEYS_DYNAMODB_TABLE is not provided in the environment variables.");
        throw new Error("API_KEYS_DYNAMODB_TABLE is not provided in the environment variables.");
    }

    try {

        const command = new QueryCommand({
            TableName: apiTable,
            IndexName: 'ApiKeyIndex',
            KeyConditionExpression: 'apiKey = :apiKeyVal',
            ExpressionAttributeValues: {
                ':apiKeyVal': { S: apiKey }
            }
        });
        

        console.log("Checking API key validity.");
        const response = await dynamodbClient.send(command);
        //FOR GSI 
        const item = response.Items[0];

        if (!item) {
            console.log("API key does not exist.");
            return {
                statusCode: 404,
                body: JSON.stringify({ message: "API key not found." })
            };
        }

        // Convert DynamoDB types to regular objects
        const apiData = unmarshall(item);

        // Check if the API key is active
        if (!apiData.active) {
            console.log("API key is inactive.");
            return {
                statusCode: 403,
                body: JSON.stringify({ message: "API key is inactive." })
            };
        }

        // Optionally check the expiration date if applicable
        if (apiData.expirationDate && new Date(apiData.expirationDate) <= new Date()) {
            console.log("API key has expired.");
            return {
                statusCode: 403,
                body: JSON.stringify({ message: "API key has expired." })
            };
        }

        const access = apiData.accessTypes.flat()

        if (!(access && (access.includes('chat') || access.includes('Full Access')))) {
            console.log("API doesn't have access to chat");
            return {
                statusCode: 403,
                body: JSON.stringify({ message: "API key does not have access to chat functionality" })
            };
        }        

        // update last accessed.
        const updateItemCommand = new UpdateItemCommand({
            TableName: apiTable,
            Key: { 'api_owner_id': { S: apiData.api_owner_id} },
            UpdateExpression: "SET lastAccessed = :now",
            ExpressionAttributeValues: {
                ':now': { S: new Date().toISOString() }
            }
        });
        console.log("Last Access updated")

        await dynamodbClient.send(updateItemCommand);

       const currentUser = determine_api_user(apiData);
       if (currentUser.statusCode) return currentUser; // means error


       /*
            {
            "model": "anthropic.claude-3-haiku-20240307-v1:0",
            "temperature": 1,
            "max_tokens": 1000,
            "stream": true,
            "dataSources": [],
            "messages": [
                {
                "role": "system",
                "content": "Follow the user's instructions carefully. Respond using markdown. If you are asked to draw a diagram, you can use Mermaid diagrams using mermaid.js syntax in a ```mermaid code block. If you are asked to visualize something, you can use a ```vega code block with Vega-lite. Don't draw a diagram or visualize anything unless explicitly asked to do so. Be concise in your responses unless told otherwise."
                },
                {
                "role": "user",
                "content": "hi!",
                "type": "prompt",
                "data": {},
                "id": "11b14f5f-b321-48f8-b581-fdb46f81627a"
                }
            ],
            "options": {
                "accountId": ---
                "requestId": "heh4f",
                "model": {
                "id": "anthropic.claude-3-haiku-20240307-v1:0",
                "name": "Claude-3-Haiku (bedrock)",
                "maxLength": 24000,
                "tokenLimit": 4000,
                "actualTokenLimit": 4096,
                "inputCost": 0.00025,
                "outputCost": 0.00125,
                "description": "Consider for high-velocity tasks with near-instant responsiveness and emphasis on security and robustness through minimized risk of harmful outputs. Features speeds 3 times faster than its Claude peer models while being the most economical choice. Best for simple queries, lightweight conversation, rapid analysis of large volumes of data, and handling of much longer prompts. Trained on information available through August 2023."
                },
                "prompt": "Follow the user's instructions carefully. Respond using markdown. If you are asked to draw a diagram, you can use Mermaid diagrams using mermaid.js syntax in a ```mermaid code block. If you are asked to visualize something, you can use a ```vega code block with Vega-lite. Don't draw a diagram or visualize anything unless explicitly asked to do so. Be concise in your responses unless told otherwise.",
                "maxTokens": 1000
            }
            }


       */
        // we add the accountId 
        let requestBody;
        try {
            if (!apiData.account) {
                const error = new Error("Account ID is missing from apiData");
                error.code = 1001; 
                throw error;
            }

            requestBody = JSON.parse(event.body);

            // this is the coa string to be recorded in the usage table 
            requestBody.options.accountId = apiData.account;
            requestBody.options.requestId = Math.random().toString(36).substring(7);
        } catch (e) {
            const error = (e.code === 1001) ? "API key data does not have a valid account attached" : "Invalid JSON in request body"
            return {
                statusCode: 400,
                body: JSON.stringify({ message: error }),
            };
        }

        // Return the validated user and additional data
        return {user: currentUser, body: requestBody, accessToken: apiKey};

    } catch (error) {
        console.error("Error during DynamoDB operation:", error);
        return {
            statusCode: 500,
            body: JSON.stringify({ message: "Internal server error occurred." })
        };
    }
};

const determine_api_user = (data) => {
    // Extract key type from api_owner_id
    const keyTypePattern = /\/(.*?)Key\//;  // This regex matches the pattern to find the key type part
    const match = data.api_owner_id.match(keyTypePattern);
    const keyType = match ? match[1] : null;

    switch (keyType) {
        case 'owner':
            return data.owner;
        case 'delegate':
            return data.delegate;
        case 'system':
            return data.systemId;
        default:
            console.error("Unknown or missing key type in api_owner_id:", keyType);
            return {
                statusCode: 400,
                body: JSON.stringify({ message: "Invalid or unrecognized key type." })
            };
    }
}