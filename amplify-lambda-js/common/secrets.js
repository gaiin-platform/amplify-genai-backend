//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// Since __dirname is not available in ES module scope, you have to construct the path differently.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Now, use the constructed path to point to your .env.local file
config({ path: join(__dirname, '../../.env.local') });


const secretsManagerClient = new SecretsManagerClient({ region: 'us-east-1' }); 

export const getSecret = async (secretName) => {

    const command = new GetSecretValueCommand({ SecretId: secretName });

    try {
        // Send the command to Secrets Manager service
        const data = await secretsManagerClient.send(command); 

        let secret;
        if ('SecretString' in data) {
            secret = data.SecretString;
        } else {
            // For binary secrets, data.SecretBinary is set instead of data.SecretString
            const buff = Buffer.from(data.SecretBinary, 'base64');
            secret = buff.toString('ascii');
            
        }
        return secret;
    } catch (error) {
        console.error(error);
        throw error;
    }
}

// The get_endpoint_data function converted to JavaScript
const getEndpointData = (parsed_data, model_name) => {
    // Find the model in the list of models
    console.log("Get endpoint data model_name: ", model_name);
    if(model_name === "gpt-4-1106-Preview" || model_name === "gpt-4-1106-preview"){
        model_name = "gpt-4-turbo";
    } else if(model_name === "gpt-35-1106") {
        model_name = "gpt-35-turbo";
    }

    const endpoint_data = parsed_data.models.find((model) => model.hasOwnProperty(model_name));
    if (!endpoint_data) {
        throw new Error("Model name not found in the secret data");
    }

    // Randomly choose one of the endpoints
    const endpoint_info = endpoint_data[model_name].endpoints[Math.floor(Math.random() * endpoint_data[model_name].endpoints.length)];
    const { url, key } = endpoint_info;
    return { key, url };
};


const secret_name = process.env.LLM_ENDPOINTS_SECRETS_NAME;
const secret_data = await getSecret(secret_name);
const parsed_secret = JSON.parse(secret_data);

// The get_llm_config function converted to JavaScript
export const getLLMConfig = async (model_name, model_provider) => {
    if (model_provider === "OpenAI") {
        const url = "https://api.openai.com/v1/responses";
        const key = await getSecretApiKey("OPENAI_API_KEY");
        return {url, key};
    }
    return getEndpointData(parsed_secret, model_name);
};


export const getSecretApiKey = async (secretName) => {
    const secret = await getSecret(process.env.SECRETS_ARN_NAME);
    try {
        const apiKey = JSON.parse(secret);
        return apiKey[secretName];
    } catch (error) {
        logger.error("Error getting secret API key: ", secretName, "\n", error);
        return null;
    }
}
