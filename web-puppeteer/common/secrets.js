const { SecretsManagerClient, GetSecretValueCommand } = require('@aws-sdk/client-secrets-manager');
const { getLogger } = require("./logging");

const logger = getLogger("secrets");

// Initialize the Secrets Manager client
const secretsManagerClient = new SecretsManagerClient({ region: 'us-east-1' });

async function getSecret(secretName) {
    const command = new GetSecretValueCommand({ SecretId: secretName });
    try {
        const data = await secretsManagerClient.send(command);
        if ('SecretString' in data) {
            return data.SecretString;
        } else {
            const buff = Buffer.from(data.SecretBinary, 'base64');
            return buff.toString('ascii');
        }
    } catch (error) {
        logger.error(error);
        throw error;
    }
}

function getEndpointData(parsedData, modelName) {
    // Modify the model name adjustments based on your requirements
    let adjustedModelName = modelName.toLowerCase().includes("gpt-4") ? "gpt-4-turbo" : modelName;
    const endpointData = parsedData.models.find(model => model.hasOwnProperty(adjustedModelName));
    if (!endpointData) {
        throw new Error("Model name not found in the secret data");
    }
    const endpointInfo = endpointData[adjustedModelName].endpoints[Math.floor(Math.random() * endpointData[adjustedModelName].endpoints.length)];
    const { url, key } = endpointInfo;
    return { key, url };
}
