import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';

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
