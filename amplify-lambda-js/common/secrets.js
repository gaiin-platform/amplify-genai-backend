//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { getLogger } from './logging.js';

const logger = getLogger("secrets");

// Since __dirname is not available in ES module scope, you have to construct the path differently.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Now, use the constructed path to point to your .env.local file
config({ path: join(__dirname, '../../.env.local') });


const secretsManagerClient = new SecretsManagerClient({ region: 'us-east-1' }); 

export const getSecret = async (secretName) => {

    // In local development, check for an environment variable override before hitting AWS.
    // For ARNs (arn:aws:...:secret:name-SUFFIX), extract the name and strip the random suffix.
    // e.g. "dev-openai-endpoints"                         -> LOCAL_SECRET_DEV_OPENAI_ENDPOINTS
    //      "arn:...:secret:dev-amplify-app-secrets-BMNOEY" -> LOCAL_SECRET_DEV_AMPLIFY_APP_SECRETS
    if (process.env.LOCAL_DEVELOPMENT === 'true') {
        let lookupName = secretName;
        if (secretName && secretName.startsWith('arn:')) {
            const arnParts = secretName.split(':');
            // Strip the random 6-character suffix appended by Secrets Manager (e.g. "-BMNOEY")
            lookupName = arnParts[arnParts.length - 1].replace(/-[A-Za-z0-9]{6}$/, '');
        }
        const envVarName = 'LOCAL_SECRET_' + (lookupName || '').replace(/[^a-zA-Z0-9]/g, '_').toUpperCase();
        if (process.env[envVarName]) {
            logger.info(`[LOCAL_DEV] Using env var override '${envVarName}' for secret '${secretName}'`);
            return process.env[envVarName];
        }
        logger.debug(`[LOCAL_DEV] No local override found for secret '${secretName}' (checked env var: ${envVarName})`);
    }

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
        logger.error(`[CRITICAL_SECRETS_ERROR] Failed to retrieve secret ${secretName}:`, error);
        const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error retrieving secret ${secretName}: ${error.message}`);
        criticalError.isLambdaTermination = true;
        throw criticalError;
    }
}

// The get_endpoint_data function converted to JavaScript
const getEndpointData = (parsed_data, model_name) => {
    // Find the model in the list of models
    logger.debug("Get endpoint data model_name: ", model_name);
    if(model_name === "gpt-4-1106-Preview" || model_name === "gpt-4-1106-preview"){
        model_name = "gpt-4-turbo";
    } else if(model_name === "gpt-35-1106") {
        model_name = "gpt-35-turbo";
    }

    const endpoint_data = parsed_data.models.find((model) => model.hasOwnProperty(model_name));
    if (!endpoint_data) {
        logger.error(`[CRITICAL_ENDPOINTS_ERROR] Model name ${model_name} not found in LLM endpoints secret data`);
        const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error - model ${model_name} not found in endpoints configuration`);
        criticalError.isLambdaTermination = true;
        throw criticalError;
    }

    // Randomly choose one of the endpoints
    const endpoint_info = endpoint_data[model_name].endpoints[Math.floor(Math.random() * endpoint_data[model_name].endpoints.length)];
    const { url, key } = endpoint_info;
    return { key, url };
};


const secret_name = process.env.LLM_ENDPOINTS_SECRETS_NAME;
let secret_data;
let parsed_secret;

try {
    logger.info(`[SECRETS_INIT] Loading LLM endpoints configuration from: ${secret_name}`);
    secret_data = await getSecret(secret_name);
    parsed_secret = JSON.parse(secret_data);
    logger.info(`[SECRETS_INIT] Successfully loaded LLM endpoints configuration`);
} catch (error) {
    if (process.env.LOCAL_DEVELOPMENT === 'true') {
        const localEnvVar = 'LOCAL_SECRET_' + (secret_name || '').replace(/[^a-zA-Z0-9]/g, '_').toUpperCase();
        logger.warn(`[LOCAL_DEV] Could not load LLM endpoints from AWS Secrets Manager: ${error.message}`);
        logger.warn(`[LOCAL_DEV] To provide a local override, set ${localEnvVar} in .env.local with the JSON endpoints payload.`);
        logger.warn(`[LOCAL_DEV] Server will start, but LLM calls using Azure/non-OpenAI providers will fail until this is configured.`);
        parsed_secret = null;
    } else {
        logger.error(`[CRITICAL_SECRETS_ERROR] Failed to load LLM endpoints configuration during module initialization:`, error);
        const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error loading LLM endpoints configuration: ${error.message}`);
        criticalError.isLambdaTermination = true;
        throw criticalError;
    }
}

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
    try {
        const secret = await getSecret(process.env.SECRETS_ARN_NAME);
        try {
            const apiKey = JSON.parse(secret);
            const keyValue = apiKey[secretName];
            if (!keyValue) {
                logger.error(`[CRITICAL_SECRETS_ERROR] API key ${secretName} not found in secrets`);
                const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error - API key ${secretName} not found in secrets configuration`);
                criticalError.isLambdaTermination = true;
                throw criticalError;
            }
            return keyValue;
        } catch (parseError) {
            logger.error(`[CRITICAL_SECRETS_ERROR] Failed to parse secrets JSON for API key ${secretName}:`, parseError);
            const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error parsing secrets JSON for API key ${secretName}: ${parseError.message}`);
            criticalError.isLambdaTermination = true;
            throw criticalError;
        }
    } catch (error) {
        // If it's already a termination error, re-throw it
        if (error.isLambdaTermination) {
            throw error;
        }
        logger.error(`[CRITICAL_SECRETS_ERROR] Failed to retrieve secrets for API key ${secretName}:`, error);
        const criticalError = new Error(`LAMBDA_TERMINATION_REQUIRED: Critical error retrieving secrets for API key ${secretName}: ${error.message}`);
        criticalError.isLambdaTermination = true;
        throw criticalError;
    }
}
