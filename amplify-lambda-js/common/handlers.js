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

        // Ensure the Authorization header exists
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

        const prefix = 'vupingidp_';
        const index = payload.username.indexOf(prefix);
        const current_user = index !== -1 ? payload.username.slice(index + prefix.length) : payload.username;
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