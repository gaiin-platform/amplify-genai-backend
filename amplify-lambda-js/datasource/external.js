import {DynamoDBClient, GetItemCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import {validateUrl} from "../common/urlValidator.js";

const dynamodbClient = new DynamoDBClient({ });
const logger = getLogger("datasource.external");

async function getDatasourceRegistryConfig(type) {

    if(process.env.DATASOURCE_REGISTRY_DYNAMO_TABLE === undefined) {
        logger.error('DATASOURCE_REGISTRY_DYNAMO_TABLE environment variable not set');
        return null;
    }


    const params = {
        TableName: process.env.DATASOURCE_REGISTRY_DYNAMO_TABLE,
        Key: {
            type: { S: type }
        }
    };

    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        if (response.Item) {
            return unmarshall(response.Item);
        } else {
            return null;
        }
    } catch (error) {
        logger.error('Error getting assistant alias:', error);
        return null;
    }
}

const createHandler = async (sourceType, config) => {

    const method = config.requestMethod || 'POST';
    const endpoint = config.endpoint;
    const includeAccessToken = config.includeAccessToken || false;
    const includeAccount = config.includeAccount || false;
    const additionalParams = config.additionalParams || {};

    // Valid options are 'lastMessage', 'allMessages', 'lastMessageContent', 'none'
    const queryMode = config.queryMode || 'lastMessage';

    // Validate endpoint URL to prevent SSRF
    const urlValidation = validateUrl(endpoint, { allowCredentialForwarding: includeAccessToken });
    if (!urlValidation.valid) {
        logger.error(`SSRF protection: blocked external datasource endpoint: ${endpoint} - ${urlValidation.reason}`);
        return null;
    }

    const requestBuilder = async (chatRequest, params, dataSource) => {
        const requestData = {
            id: dataSource.id.slice(sourceType.length),
            dataSource: dataSource,
            ...additionalParams
        }

        if(queryMode === 'lastMessage') {
            // Pull the last message from the chatRequest
            requestData.query = chatRequest.messages.slice(-1)[0];
        }
        else if(queryMode === 'allMessages') {
            // Pull the all messages from the chatRequest
            requestData.query = chatRequest.messages;
        }
        else if(queryMode === 'lastMessageContent') {
            requestData.query = chatRequest.messages.slice(-1)[0].content;
        }
        else if(queryMode === 'none') {
            // Do nothing
        }

        if(includeAccount) {
            requestData.account = params.account;
        }

        const headers ={'Content-Type': 'application/json'};

        if(includeAccessToken) {
            headers['Authorization'] = 'Bearer ' + params.account.accessToken;
        }

        const request = {endpoint, method, headers};

        if (method === 'POST') {
            request['body'] = JSON.stringify({data:requestData});
        }
        else if(method === 'GET') {
            const url = new URL(endpoint);
            url.search = new URLSearchParams(requestData).toString();
            request['endpoint'] = url.toString();
        }

        return request;
    }

    return async (chatRequest, params, dataSource) => {

        const request = await requestBuilder(chatRequest, params, dataSource);
        const resolvedEndpoint = request.endpoint;
        delete request.endpoint;

        // Re-validate the final resolved endpoint (in case GET params changed the URL)
        const finalValidation = validateUrl(resolvedEndpoint, { allowCredentialForwarding: includeAccessToken });
        if (!finalValidation.valid) {
            logger.error(`SSRF protection: blocked resolved endpoint: ${resolvedEndpoint} - ${finalValidation.reason}`);
            throw new Error(`Request blocked by SSRF protection: ${finalValidation.reason}`);
        }

        const response = await fetch(resolvedEndpoint, request);
        const data = await response.json();

        const location = (data && data.location) ?
            data.location : { key: dataSource.id };

        const content = JSON.stringify(data.data);

        const canSplit = (data && data.canSplit) || false;

        const contents = [{
            content,
            location,
            canSplit
        }];

        const result =
            {
                "name": dataSource.id,
                "content": contents
            }

        return result;
    }
}


const getDatasourceHandler = async (sourceType, chatRequest, params, dataSource) => {
    const type = sourceType.split('://')[0];
    const config = await getDatasourceRegistryConfig(type);

    const handler =  config ? await createHandler(sourceType, config) : null;
    return handler;
}

export default getDatasourceHandler;