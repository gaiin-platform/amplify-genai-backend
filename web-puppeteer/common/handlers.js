const { CognitoJwtVerifier } = require("aws-jwt-verify");
const { getLogger } = require("./logging");

const logger = getLogger("handlers"); // Initialize logger

// Ensure the environment variables are defined
const userPoolId = process.env.COGNITO_USER_POOL_ID;
const clientId = process.env.COGNITO_CLIENT_ID;
if (!userPoolId || !clientId) {
    throw new Error("Environment variables USER_POOL_ID and CLIENT_ID must be defined");
}

const verifier = CognitoJwtVerifier.create({
    userPoolId: userPoolId,
    tokenUse: "access",
    clientId: clientId,
});

async function extractParams(event) {
    const authorizationHeader = event.headers.Authorization || event.headers.authorization;
    if (!authorizationHeader) {
        return {
            statusCode: 401,
            body: { message: "Unauthorized" },
        };
    }

    const match = authorizationHeader.match(/^Bearer (.+)$/i);
    if (!match) {
        return {
            statusCode: 401,
            body: { message: "Unauthorized: Bearer token not found" },
        };
    }

    const token = match[1];
    try {
        const payload = await verifier.verify(token);
        const currentUser = payload.username.replace('vupingidp_', '');
        logger.debug("Current user: " + currentUser);

        const requestBody = JSON.parse(event.body);
        return { user: currentUser, body: requestBody, accessToken: token };
    } catch (error) {
        logger.error(error);
        return {
            statusCode: 401,
            body: { message: "Unauthorized. Invalid token." },
        };
    }
}

module.exports = { extractParams };
