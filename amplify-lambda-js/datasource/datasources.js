import {S3Client, GetObjectCommand} from '@aws-sdk/client-s3';
import {DynamoDBClient, GetItemCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import {canReadDataSources} from "../common/permissions.js";

const logger = getLogger("datasources");

const client = new S3Client();
const dynamodbClient = new DynamoDBClient();

const dataSourcesQueryEndpoint = process.env.DATASOURCES_QUERY_ENDPOINT;
const hashFilesTableName = process.env.HASH_FILES_DYNAMO_TABLE;

export const getDataSourcesInConversation = (chatBody, includeCurrentMessage = true) => {
    if (chatBody && chatBody.messages) {
        const base = (includeCurrentMessage ? chatBody.messages : chatBody.messages.slice(0, -1))

        return base
            .filter(m => {
                return m.data && m.data.dataSources
            }).flatMap(m => m.data.dataSources)
    }

    return [];
}

export const getFileText = async (key) => {

    const textKey = key.endsWith(".content.json") ?
        key :
        key.trim() + ".content.json";
    const bucket = process.env.S3_FILE_TEXT_BUCKET_NAME;
    logger.debug("Fetching file from S3", {bucket: bucket, key: textKey});

    const command = new GetObjectCommand({
        Bucket: bucket,
        Key: textKey,
    });

    try {
        const response = await client.send(command);
        const str = await response.Body.transformToString();
        const data = JSON.parse(str);
        return data;
    } catch (error) {
        logger.error(error, {bucket: bucket, key: textKey});
        return null;
    }
}

export const getDataSourcesByTag = async (params, body, tag) => {
    const bodyData = {
        "data": {
            "tags": [tag]
        }
    };

    try {

        const response = await fetch(dataSourcesQueryEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${params.accessToken}`
            },
            body: JSON.stringify(bodyData)
        });

        if (response.ok) {
            const data = await response.json();
            if (data.success && data.data.items) {
                return data.data.items.map((item) => {
                    const fullId = (item.id.indexOf("://") > -1) ? item.id : "s3://" + item.id;

                    return {
                        id: fullId,
                        type: item.type,
                        metadata: {}
                    }
                });
            }
        }
    } catch (e) {
        logger.error("Unable to fetch data sources by tag", {tag: tag});
        logger.error(e);
    }

    return [];
}

export const resolveDataSourceAliases = async (params, body, dataSources) => {

    if(!dataSources){
        return [];
    }

    const results = dataSources.map(async (ds) => {
        if (ds.id && ds.id.startsWith("tag://")) {
            return await getDataSourcesByTag(params, body, ds.id.slice(6).trim());
        } else {
            return [ds];
        }
    });

    const flattened = (await Promise.all(results)).flatMap((item) => item);
    return flattened;
}

export const resolveDataSources = async (params, body, dataSources) => {
    dataSources = await translateUserDataSourcesToHashDataSources(params, body, dataSources);

    const convoDataSources = await translateUserDataSourcesToHashDataSources(
        params, body, getDataSourcesInConversation(body, true)
    );

    let allDataSources = [
        ...dataSources,
        ...convoDataSources.filter(ds => !dataSources.find(d => d.id === ds.id))
    ]

    const nonUserSources = allDataSources.filter(ds =>
        !extractKey(ds.id).startsWith(params.user + "/")
    );

    if (nonUserSources && nonUserSources.length > 0) {
        if (!await canReadDataSources(params.accessToken, nonUserSources)) {
            throw new Error("Unauthorized data source access.");
        }
    }

    return allDataSources;
}

export const extractProtocol = (url) => {
    try {
        // Find the index where '://' appears.
        const protocolEndIndex = url.indexOf('://');

        // If '://' is not found, it might not be a valid URL, but we still handle cases like 'mailto:'
        if (protocolEndIndex === -1) {
            const colonIndex = url.indexOf(':');
            if (colonIndex === -1) {
                return null;
            }
            return url.substring(0, colonIndex + 1); // Include the colon
        }

        // Extract and return the protocol (including the '://')
        return url.substring(0, protocolEndIndex + 3); // Include the '://'
    } catch (error) {
        return null;
    }
}

export const extractKey = (url) => {
    const proto = extractProtocol(url) || '';
    return url.slice(proto.length);
}


const getChunkAggregator = (maxTokens, options) => {

    let maxItemsPerChunk = Number.MAX_VALUE;
    if (options && options.chunking && options.chunking.maxItemsPerChunk) {
        maxItemsPerChunk = options.chunking.maxItemsPerChunk;
    }

    return (dataSource, {currentChunk = "", currentTokenCount = 0, chunks = [], itemCount = 0},
            formattedSourceName,
            formattedContent,
            contentTokenCount) => {
        if ((currentTokenCount + contentTokenCount > maxTokens) || (itemCount + 1 > maxItemsPerChunk)) {
            // If the current chunk is too big, push it to the chunks array and start a new one
            if (currentChunk.length > 0) {
                chunks.push({
                    id: dataSource.id + "?chunk=" + chunks.length,
                    context: formattedSourceName + currentChunk,
                    tokens: currentTokenCount
                });
                itemCount = 1;
            }

            currentChunk = formattedContent;
            currentTokenCount = contentTokenCount;
        } else {
            // Add to the current chunk
            currentChunk += (currentChunk ? "\n" : "") + formattedContent;
            currentTokenCount += contentTokenCount;
            itemCount = itemCount + 1;
        }
        return {chunks, currentChunk, currentTokenCount, itemCount}
    }

}

export const formatAndChunkDataSource = (tokenCounter, dataSource, content, maxTokens, options) => {
    logger.debug("Chunking/Formatting data from: " + dataSource.id);

    if (content && content.content && content.content.length > 0) {
        const firstLocation = content.content[0].location ? content.content[0].location : null;

        let contentFormatter;
        if (firstLocation && firstLocation.slide) {
            logger.debug("Formatting data from: " + dataSource.id + " as slides");
            contentFormatter = c => 'File: ' + content.name + ' Slide: ' + c.location.slide + '\n--------------\n' + c.content;
        } else if (firstLocation && firstLocation.page) {
            logger.debug("Formatting data from: " + dataSource.id + " as pages");
            contentFormatter = c => 'File: ' + content.name + ' Page: ' + c.location.page + '\n--------------\n' + c.content;
        } else if (firstLocation && firstLocation.row_number) {
            logger.debug("Formatting data from: " + dataSource.id + " as rows");
            contentFormatter = c => c.content;
        } else {
            logger.debug("Formatting data from: " + dataSource.id + " as raw text");
            contentFormatter = c => c.content;
        }

        const chunks = [];
        let currentChunk = '';
        let currentTokenCount = 0;
        let state = {chunks, currentChunk, currentTokenCount};

        const aggregator = getChunkAggregator(maxTokens, options);
        const formattedSourceName = "Source:" + content.name + " Type:" + dataSource.type + "\n-------------\n";

        for (const part of content.content) {
            const formattedContent = contentFormatter(part);
            const contentTokenCount = tokenCounter(formattedContent);
            state = aggregator(dataSource, state, formattedSourceName, formattedContent, contentTokenCount);
        }

        // Add the last chunk if it has content
        if (state.currentChunk) {
            chunks.push({
                id: dataSource.id + "?chunk=" + chunks.length,
                context: formattedSourceName + state.currentChunk,
                tokens: state.currentTokenCount
            });
        }

        return chunks;

    } else {
        return formatAndChunkDataSource(tokenCounter, dataSource, {
            content: [{
                content: content,
                location: {}
            }]
        }, maxTokens);
    }
}

export const translateUserDataSourcesToHashDataSources = async (params, body, dataSources) => {

    dataSources = await resolveDataSourceAliases(params, body, dataSources);

    const translated = await Promise.all(dataSources.map(async (ds) => {

        let key = ds.id;

        try {
            if (key.startsWith("s3://")) {

                key = extractKey(key);

                const command = new GetItemCommand({
                    TableName: hashFilesTableName, // Replace with your table name
                    Key: {
                        id: {S: key}
                    }
                });

                // Send the command to DynamoDB and wait for the response
                const {Item} = await dynamodbClient.send(command);

                if (Item) {
                    // Convert the returned item from DynamoDB's format to a regular JavaScript object
                    const item = unmarshall(Item);
                    return {...ds, id: "s3://" + item.textLocationKey};
                } else {
                    return ds; // No item found with the given ID
                }
            } else {
                return ds;
            }
        } catch (e) {
            return ds;
        }
    }));

    return translated.filter((ds) => ds != null);
}

export const getContent = async (dataSource) => {
    const sourceType = extractProtocol(dataSource.id);

    logger.debug("Fetching data from: " + dataSource.id + " (" + sourceType + ")");

    if (sourceType === 's3://') {

        logger.debug("Fetching data from S3");
        const result = await getFileText(dataSource.id.slice(sourceType.length));
        logger.debug("Fetched data from S3: " + (result != null));

        if (result == null) {
            throw new Error("Could not fetch data from S3");
        }

        return result;
    } else if (sourceType === 'obj://') {
        logger.debug("Fetching data from object");

        const sourceKey = extractKey(dataSource.id);

        const contents = Object.entries(dataSource.content)
            .filter(([k, v]) => !k.startsWith("__tokens_"))
            .map(([k, v]) => {
                return {
                    "content": v,
                    "tokens": dataSource.content["__tokens_" + k] || 1000,
                    "location": {
                        "key": sourceKey
                    },
                    "canSplit": true
                }
            });

        const content =
            {
                "name": dataSource.id,
                "content": contents
            }

        return content;
    } else {
        return [dataSource];
    }
}

export const getContexts = async (tokenCounter, dataSource, maxTokens, options) => {
    const sourceType = extractProtocol(dataSource.id);

    logger.debug("Get contexts with options", options);

    logger.debug("Fetching data from: " + dataSource.id + " (" + sourceType + ")");


    const result = await getContent(dataSource);
    return formatAndChunkDataSource(tokenCounter, dataSource, result, maxTokens, options);
}