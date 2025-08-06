//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {DynamoDBClient, QueryCommand, UpdateItemCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import { CognitoJwtVerifier } from "aws-jwt-verify";
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { TokenV1 } from './api_utils.js';

// Since __dirname is not available in ES module scope, you have to construct the path differently.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Now, use the constructed path to point to your .env.local file
config({ path: join(__dirname, '../../.env.local') });


// Read environment variables
const userPoolId = process.env.COGNITO_USER_POOL_ID;
const clientId = process.env.COGNITO_CLIENT_ID;
const idpPrefix = (process.env.IDP_PREFIX || '').toLowerCase();

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

        const user = payload.username;
        const current_user = idpPrefix && user.startsWith(idpPrefix) ? user.slice(idpPrefix.length + 1) : user;
        console.log("Current user: " + current_user);

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
        // determine if we have a new or old key type
        let lookupValue = apiKey;
        if (lookupValue.substring(0, 7) === "amp-v1-") {
            // this is a new key, we need to look up the hash
            const tokenV1 = new TokenV1(lookupValue);
            lookupValue = tokenV1.key; // hash
        }

        const command = new QueryCommand({
            TableName: apiTable,
            IndexName: 'ApiKeyIndex',
            KeyConditionExpression: 'apiKey = :apiKeyVal',
            ExpressionAttributeValues: {
                ':apiKeyVal': { S: lookupValue }
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

        if (!(access && (access.includes('chat') || access.includes('full_access')))) {
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
            requestBody.options.accountId = apiData.account.id;
            requestBody.options.requestId = Math.random().toString(36).substring(7);
        } catch (e) {
            const error = (e.code === 1001) ? "API key data does not have a valid account attached" : "Invalid JSON in request body"
            return {
                statusCode: 400,
                body: JSON.stringify({ message: error }),
            };
        }
        requestBody.options.api_accessed = true;
        requestBody.options.rateLimit = apiData.rateLimit;
        console.log("Current User: ", currentUser);
        // Return the validated user and additional data
        return {user: currentUser, body: requestBody, accessToken: apiKey, apiKeyId: apiData.api_owner_id}; 

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