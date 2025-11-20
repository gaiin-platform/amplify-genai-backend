//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {S3Client, GetObjectCommand} from '@aws-sdk/client-s3';
import {DynamoDBClient, GetItemCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import {canReadDataSources} from "../common/permissions.js";
import {lru} from "tiny-lru";
import getDatasourceHandler from "./external.js";
import { getModelByType, ModelTypes, getChatFn, setModel} from "../common/params.js";
import { LLM } from "../common/llm.js";

const logger = getLogger("datasources");

const client = new S3Client();
const dynamodbClient = new DynamoDBClient();

export const additionalImageInstruction = "\n\n Additional Image Instructions:\n If given an encode image, describe the image in vivid detail, capturing every element, including the subjects, colors, textures, and emotions. Provide enough information so that someone can visualize the image perfectly without seeing it, using precise and rich language. This should be its own block of text."

const dataSourcesQueryEndpoint = process.env.API_BASE_URL + "/files/query";
const hashFilesTableName = process.env.HASH_FILES_DYNAMO_TABLE;

const dataSourcesWithTagCache = lru(500, 30000, false);
const hashDataSourcesCache = lru(500, 0, false);

/**
 * Get all data sources that are referenced by messages in the conversation.
 * @param chatBody
 * @param includeCurrentMessage
 * @returns {*[]|*}
 */
export const getDataSourcesInConversation = (chatBody, includeCurrentMessage = true) => {
    if (chatBody && chatBody.messages) {
        const base = (includeCurrentMessage ? chatBody.messages : chatBody.messages.slice(0, -1))

        return base
            .filter(m => {
                return m.data && m.data.dataSources 
            }).flatMap(m => m.data.dataSources).filter(ds => !isImage(ds))
    }

    return [];
}

/**
 * Fetches the text content of a an S3 data source.
 * @param key
 * @returns {Promise<null|any>}
 */
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

export const doesNotSupportImagesInstructions = (modelName) => {
    return `\n\n At the end of your response, please let the user know the model ${modelName} does not support images. Advise them to try another model.`;
}

export const getImageBase64Content = async (dataSource) => {
    const bucket = process.env.S3_IMAGE_INPUT_BUCKET_NAME;
    const key = extractKey(dataSource.id)
    logger.debug("Fetching file from S3", {bucket: bucket, key: key});

    const command = new GetObjectCommand({
        Bucket: bucket,
        Key: key,
    });

    try {
        const response = await client.send(command);
        const streamToString = (stream) => 
            new Promise((resolve, reject) => {
                const chunks = [];
                stream.on("data", (chunk) => chunks.push(chunk));
                stream.on("error", reject);
                stream.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
            });
        
        const data = await streamToString(response.Body);
        logger.debug("Base64 encoded image retrieved")
        return data;
    } catch (error) {
        logger.error("Error retrieving base64 encoded image", error);
        return null;
    }

}

export const isDocument = ds =>
    (ds && ds.id && ds.id.indexOf("://") < 0) ||
    (ds && ds.key && ds.key.indexOf("://") < 0) ||
    (ds && ds.id && ds.id.startsWith("s3://")) ||
    (ds && ds.key && ds.key.startsWith("s3://"));


export const isImage = ds => ds && ds.type && ds.type.startsWith("image/")

/**
 * This function looks at the data sources in the chat request and all of the data sources in the conversation
 * and then determines which data sources should be inserted in their entirety into the chat request and which
 * should be used for RAG only.
 *
 * Here is how this is done:
 * 1. By default, all data sources included in the chat request are inserted into the chat request, unless
 *    "ragOnly" is set to true at the chat request level
 * 2. All data sources referenced in messages in the conversation are included for RAG unless "skipRag" is set
 *    to true at the chat request level
 * 3. Any data sources that are "tag:xyz?ragOnly=true" are included for RAG only after resolving the tag to
 *    a concrete set of data sources, otherwise, they are inserted in the conversation.
 *
 * @param params
 * @param chatRequestOrig
 * @param dataSources
 * @returns {Promise<{ragDataSources: *[], dataSources: *[]}>}
 */
export const getDataSourcesByUse = async (params, chatRequestOrig, dataSources) => {

    logger.debug("Getting data sources by use", dataSources);

    if ((params.options.skipRag && params.options.ragOnly) || params.options.noDataSources){
        return {
            ragDataSources: [],
            dataSources: []
        };
    }

    const msgDataSources =
        await translateUserDataSourcesToHashDataSources(
            params,
            chatRequestOrig,
            chatRequestOrig.messages.slice(-1)[0].data?.dataSources || []
        );

    const referencedDataSourcesInMessages = chatRequestOrig.messages.slice(0,-1)
        .filter( m => {
            return m.data && m.data.dataSources
        }).flatMap(m => m.data.dataSources).filter(ds => !isImage(ds));

    const convoDataSources = await translateUserDataSourcesToHashDataSources(
        params,
        chatRequestOrig,
        referencedDataSourcesInMessages
    );

    dataSources = await translateUserDataSourcesToHashDataSources(params, chatRequestOrig, dataSources);

    const getRagOnly = sources => sources.filter(ds =>
        params.options.ragOnly || (ds.metadata && ds.metadata.ragOnly));

    const getInsertOnly = sources => sources.filter(ds =>
        !params.options.ragOnly && (!ds.metadata || !ds.metadata.ragOnly));

    const getDocumentDataSources = sources => sources.filter(isDocument);

    const getNonDocumentDataSources = sources => sources.filter(ds => !isDocument(ds));

    const uniqueDataSources = dss => {return [...Object.values(dss.reduce(
        (acc, ds) => (acc[ds.id] = ds, acc), {})
    )];}

    const attachedDataSources = [
        ...dataSources,
        ...msgDataSources];

    const nonUniqueRagDataSources = [
        ...(getRagOnly(dataSources)),
        ...(getRagOnly(msgDataSources)),
        ...(getDocumentDataSources(convoDataSources))
    ];

    let ragDataSources = Object.values(nonUniqueRagDataSources.reduce(
        (acc, ds) => (acc[ds.id] = ds, acc), {})
    );

    const allDataSources = [
        ...(getInsertOnly(dataSources)),
        ...(getInsertOnly(msgDataSources)),
        ...(getNonDocumentDataSources(convoDataSources))
    ];

    dataSources = Object.values(allDataSources.reduce(
        (acc, ds) => (acc[ds.id] = ds, acc), {})
    );

    if(params.options.skipRag) {
        ragDataSources = [];
    }

    const uniqueAttachedDataSources = uniqueDataSources(attachedDataSources);
    const uniqueConvoDataSources = uniqueDataSources(convoDataSources);

    if(params.options.dataSourceOptions || chatRequestOrig.options.dataSourceOptions) {

        const dataSourceOptions = {
        ...(chatRequestOrig.options.dataSourceOptions || {}),
        ...(params.options.dataSourceOptions || {})};

        logger.debug("Applying data source options", dataSourceOptions);

        const insertList = [];
        const ragList = [];

        if (dataSourceOptions.disableDataSources) {
            ragDataSources = [];
            dataSources = [];
        }
        else {
            if (dataSourceOptions.insertConversationDocuments) {
                insertList.push(...uniqueConvoDataSources);
            }
            if (dataSourceOptions.insertAttachedDocuments) {
                insertList.push(...uniqueAttachedDataSources);
            }
            if(dataSourceOptions.ragConversationDocuments) {
                ragList.push(...uniqueConvoDataSources);
            }
            if(dataSourceOptions.ragAttachedDocuments) {
                ragList.push(...uniqueAttachedDataSources);
            }
        }

        ragDataSources = uniqueDataSources(ragList);
        dataSources = uniqueDataSources(insertList);
    }

    return {
        ragDataSources,
        dataSources,
        conversationDataSources: uniqueConvoDataSources,
        attachedDataSources: uniqueAttachedDataSources,
        allDataSources: uniqueDataSources([...uniqueAttachedDataSources, ...uniqueConvoDataSources])
    };
}

export const getTagName = (tag) => {
    return tag.indexOf("?") > 0 ? tag.split("?")[0] : tag;
}

export const getTagMetadata = (tag) => {
    const metadata = tag.indexOf("?") > 0 ?
        tag.split("?")[1].split(/[;&]/).reduce((obj, pair) => (pair = pair.split('='), obj[pair[0]] = pair[1] ? JSON.parse(pair[1]) : true, obj), {})
        : {};
    return metadata;
}

/**
 * Fetches all data sources that are tagged with a specific tag from the data sources query endpoint.
 * @param params
 * @param body
 * @param tag
 * @returns {Promise<*[]|any>}
 */
export const getDataSourcesByTag = async (params, body, tag) => {

    // Hash key
    const tagName = getTagName(tag);
    const cacheKey = (params.user || params.account.user) + "__" + tagName;

    const cached = dataSourcesWithTagCache.get(cacheKey);
    if(cached){
        return cached;
    }

    const bodyData = {
        "data": {
            "tags": [tagName]
        }
    };

    try {

        // Parse the query string into an object
        const metadata = getTagMetadata(tag);

        const response = await fetch(dataSourcesQueryEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${params.accessToken || params.account.accessToken}`
            },
            body: JSON.stringify(bodyData)
        });

        if (response.ok) {

            const data = await response.json();
            if (data.success && data.data.items) {
                const result = data.data.items.map((item) => {
                    const fullId = (item.id.indexOf("://") > -1) ? item.id : "s3://" + item.id;

                    return {
                        id: fullId,
                        type: item.type,
                        metadata
                    }
                });
                dataSourcesWithTagCache.set(cacheKey, result);
                return result;
            }
        }
        else {
            logger.error("Unable to fetch data sources by tag", {tag: tag});
            // Extract the error message
            const errorMessage = await response.text();
            logger.error(errorMessage);
        }
    } catch (e) {
        logger.error("Unable to fetch data sources by tag", {tag: tag});
        logger.error(e);
    }

    return [];
}

/**
 * Looks through the data sources for "tag:xyz" and translates them into the actual data sources by
 * querying for the list of data sources that are tagged with "xyz".
 *
 * @param params
 * @param body
 * @param dataSources
 * @returns {Promise<unknown extends (object & {then(onfulfilled: infer F): any}) ? (F extends ((value: infer V, ...args: any) => any) ? Awaited<V> : never) : unknown[]|*[]>}
 */
export const resolveDataSourceAliases = async (params, body, dataSources) => {
    if (!dataSources) return [];

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

/**
 * Resolves the data sources in the chat request and the conversation to the actual data sources
 * that will be used in the chat request.
 *
 * This involves the following steps:
 *
 * 1. Resolving any tags
 * 2. Translating any user data sources into the global hash data sources
 *
 * @param params
 * @param body
 * @param dataSources
 * @returns {Promise<(Awaited<any>|Awaited<unknown>)[]>}
 */
export const resolveDataSources = async (params, body, dataSources) => {
    logger.info("Resolving data sources", {dataSources: dataSources});

    // seperate the image ds
    if (body && body.messages && body.messages.length > 0) {
        const lastMsg = body.messages[body.messages.length - 1];
        const ds = lastMsg.data && lastMsg.data.dataSources;
        if (ds) {
            body.imageSources = ds.filter(d => isImage(d));
        } else if (body.options?.api_accessed){ // support images coming from the /chat endpoint
            const imageSources = dataSources.filter(d => isImage(d));
            if (imageSources.length > 0) body.imageSources = imageSources;
        }
        console.log("IMAGE: body.imageSources", body.imageSources);
    }

    dataSources = dataSources.filter(ds => !isImage(ds))
    
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

    if (nonUserSources && nonUserSources.length > 0  || 
        (body.imageSources && body.imageSources.length > 0)) {
        //need to ensure we extract the key, so far I have seen all ds start with s3:// but can_access_object table has it without 
        const ds_with_keys = nonUserSources.map(ds => ({ ...ds, id: extractKey(ds.id) }));
        const image_ds_keys = body.imageSources ? body.imageSources.map(ds =>  ({ ...ds, id: extractKey(ds.id) })) : [];
        console.log("IMAGE: ds_with_keys", image_ds_keys);
        if (!await canReadDataSources(params.accessToken, [...ds_with_keys, ...image_ds_keys])) {
            throw new Error("Unauthorized data source access.");
        }
    }

    return dataSources;
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

/**
 * The chunk aggregator function is used to aggregate content into chunks of a maximum size or
 * total number of chunks. This is a helper for the formatAndChunkDataSource function.
 *
 * @param maxTokens
 * @param options
 * @returns {function(*, {currentChunk?: *, currentTokenCount?: *, chunks?: *, itemCount?: *}, *, *, *): {currentTokenCount: *, chunks: *[], currentChunk: *, itemCount: number|*}}
 */
const getChunkAggregator = (maxTokens, options) => {

    let maxItemsPerChunk = Number.MAX_VALUE;
    if (options && options.chunking && options.chunking.maxItemsPerChunk) {
        maxItemsPerChunk = options.chunking.maxItemsPerChunk;
    }

    return (dataSource,
            {locations, currentChunk = "", currentTokenCount = 0, chunks = [], itemCount = 0},
            formattedSourceName,
            formattedContent,
            location,
            contentTokenCount) => {
        if ((currentTokenCount + contentTokenCount > maxTokens) || (itemCount + 1 > maxItemsPerChunk)) {
            // If the current chunk is too big, push it to the chunks array and start a new one
            if (currentChunk.length > 0) {
                chunks.push({
                    dataSource,
                    id: dataSource.id + "?chunk=" + chunks.length,
                    context: formattedSourceName + currentChunk,
                    tokens: currentTokenCount,
                    locations: locations
                });
                itemCount = 1;
            }

            locations = [location];
            currentChunk = formattedContent;
            currentTokenCount = contentTokenCount;
        } else {
            // Add to the current chunk
            locations.push(location);
            currentChunk += (currentChunk ? "\n" : "") + formattedContent;
            currentTokenCount += contentTokenCount;
            itemCount = itemCount + 1;
        }
        return {locations, chunks, currentChunk, currentTokenCount, itemCount}
    }

}

/**
 * Formats and chunks the data source content into chunks of a maxTokens or total number of chunks as
 * specified in the options.
 *
 * The result of this function is an array of chunks, each of which has a context and a token count
 * and will fit into the given token limit for the model.
 *
 * @param tokenCounter
 * @param dataSource
 * @param content
 * @param maxTokens
 * @param options
 * @returns {*[]|*}
 */
export const formatAndChunkDataSource = (tokenCounter, dataSource, content, maxTokens, options) => {
    logger.debug("Chunking/Formatting data from: " + dataSource.id);

    // Handle both content structures:
    // 1. Normal: { name: "...", content: [...] }
    // 2. Filtered: [...]  (direct array from getExtractedRelevantContext)
    let contentArray;
    let contentName;
    
    if (Array.isArray(content)) {
        // Direct array case (filtered content)
        contentArray = content;
        contentName = dataSource.id;
    } else if (content && content.content && Array.isArray(content.content)) {
        // Normal case with nested structure
        contentArray = content.content;
        contentName = content.name;
    } else {
        // Fallback for other cases
        return formatAndChunkDataSource(tokenCounter, dataSource, {
            content: [{
                content: content,
                location: {},
                dataSource
            }]
        }, maxTokens);
    }

    if (contentArray.length > 0) {
        const firstLocation = contentArray[0].location ? contentArray[0].location : null;

        let contentFormatter;
        if (firstLocation && firstLocation.slide) {
            logger.debug("Formatting data from: " + dataSource.id + " as slides");
            contentFormatter = c => 'File: ' + contentName + ' Slide: ' + c.location.slide + '\n--------------\n' + c.content;
        } else if (firstLocation && firstLocation.page) {
            logger.debug("Formatting data from: " + dataSource.id + " as pages");
            contentFormatter = c => 'File: ' + contentName + ' Page: ' + c.location.page + '\n--------------\n' + c.content;
        } else if (firstLocation && firstLocation.row_number) {
            logger.debug("Formatting data from: " + dataSource.id + " as rows");
            contentFormatter = c => c.content;
        } else {
            logger.debug("Formatting data from: " + dataSource.id + " as raw text");
            contentFormatter = c => c.content;
        }

        const chunks = [];
        const locations = [];
        let currentChunk = '';
        let currentTokenCount = 0;
        let state = {chunks, currentChunk, currentTokenCount, locations};

        const aggregator = getChunkAggregator(maxTokens, options);
        const formattedSourceName = "Source:" + contentName + " Type:" + dataSource.type + "\n-------------\n";

        for (const part of contentArray) {
            const formattedContent = contentFormatter(part);
            const contentTokenCount = tokenCounter(formattedContent);
            state = aggregator(dataSource, state, formattedSourceName, formattedContent, part.location || {}, contentTokenCount);
        }

        // Add the last chunk if it has content
        if (state.currentChunk) {
            chunks.push({
                id: dataSource.id + "?chunk=" + chunks.length,
                context: formattedSourceName + state.currentChunk,
                tokens: state.currentTokenCount,
                locations: state.locations,
                dataSource
            });
        }

        return chunks;

    } else {
        return formatAndChunkDataSource(tokenCounter, dataSource, {
            content: [{
                content: content,
                location: {},
                dataSource
            }]
        }, maxTokens);
    }
}

/**
 * All files are hashed on upload. The hash of each file is stored in the db and used to
 * determine if a duplicate file is being uploaded. If the file is a duplicate, the system
 * will skip indexing it for RAG and use its original index instead.
 *
 * This function translates from the "user data source" keys for files to the actual "hash data source"
 * keys of the shared copies of files. The result of this function will be data sources that have
 * any user-based "ids" replaced with the global hash ids for the actual global copy of the file.
 *
 * @param params
 * @param body
 * @param dataSources
 * @returns {Promise<Awaited<unknown>[]>}
 */
export const translateUserDataSourcesToHashDataSources = async (params, body, dataSources) => {
    const toResolve = dataSources ? dataSources.filter(ds => !isImage(ds)) : [];
    if (toResolve.length === 0) return [];
    dataSources = await resolveDataSourceAliases(params, body, toResolve);

    const translated = await Promise.all(dataSources.map(async (ds) => {

        let key = ds.id;

        try {
            if (key.startsWith("s3://")) {
                key = extractKey(key);
            }

            // Check the hash keys cache
            const cached = hashDataSourcesCache.get(key);
            if (cached) {
                return cached;
            }

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
                const result = {
                    ...ds,
                    metadata: {...ds.metadata, userDataSourceId: ds.id},
                    id: "s3://" + item.textLocationKey};
                hashDataSourcesCache.set(key, result);
                return result;
            } else {
                hashDataSourcesCache.set(key, ds);
                return ds; // No item found with the given ID
            }
            return ds;
        } catch (e) {
            console.log(e);
            return ds;
        }
    }));

    return translated.filter((ds) => ds != null);
}

/**
 * Resolves the content of each data source and returns the content text.
 *
 * @param dataSource
 * @returns {Promise<{name, content: {canSplit: boolean, tokens, location: {key: *}, content: *}[]}|*[]|*>}
 */
export const getContent = async (chatRequest, params, dataSource) => {
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
    }
    else if (sourceType) {
        const handler = await getDatasourceHandler(sourceType, chatRequest, params, dataSource);
        if (handler) {
            return await handler(chatRequest, params, dataSource);
        }
    }

    return [dataSource];
}

/**
 * Given a chat request and max tokens, go and fetch all of the data sources specified, chunk them,
 * and create the "contexts" that will be prompted against.
 *
 * @param tokenCounter
 * @param dataSource
 * @param maxTokens
 * @param options
 * @returns {Promise<*[]|*>}
 */
export const getContexts = async (resolutionEnv, dataSource, maxTokens, options, extractRelevantContext = false) => {
    const tokenCounter = resolutionEnv.tokenCounter;
    const sourceType = extractProtocol(dataSource.id);

    logger.debug("Get contexts with options", options);
    logger.debug("Fetching data from: " + dataSource.id + " (" + sourceType + ")");


    const result = await getContent(resolutionEnv.chatRequest, resolutionEnv.params, dataSource);
    
    if (extractRelevantContext) {
        try {
            const filteredContent = await getExtractedRelevantContext(resolutionEnv, {...result});
            if (!filteredContent) return null;
            const formattedContext = formatAndChunkDataSource(tokenCounter, dataSource, filteredContent, maxTokens, options);
            const originalTotalTokens = dataSource?.metadata?.totalTokens;
            const filteredTotalTokens = filteredContent.totalTokens;
            logger.debug(`Original document total tokens: ${originalTotalTokens} \nFiltered document tokens: ${filteredTotalTokens}\n${Math.round(filteredTotalTokens/originalTotalTokens*100)}% of original`);
            formattedContext[0].content = combineNeighboringLocations(filteredContent.content);
  
            return formattedContext;
        } catch (error) {
            logger.error("Error extracting relevant context:", error);
            logger.debug("Dumping entire document context as a fallback...");
        }
    }

    return formatAndChunkDataSource(tokenCounter, dataSource, result, maxTokens, options);
}

/**
 * Extracts the relevant context from the document.
 * @param resolutionEnv
 * @param context
 * @param dataSource
 * @returns {Promise<*[]|*>}
 */
const getExtractedRelevantContext = async (resolutionEnv, context) => {
    const params = resolutionEnv.params;
    const chatBody = resolutionEnv.chatRequest;
    const userMessage = chatBody.messages.slice(-1)[0].content;
    // Use a cheaper model for document analysis with built-in caching
    
    const model = getModelByType(params, ModelTypes.DOCUMENT_CACHING);
    
    // Create parameters for the LLM with caching enabled
    const llmParams = setModel ({
        ...params,
        options: {
            skipRag: true,
            ragOnly: false,
            dataSourceOptions:{},
        }
    }, model);
    
    // Initialize LLM for document analysis
    const chatFn = async (body, writable, context) => {
        return await getChatFn(model, body, writable, context);
    };
    
    const llm = new LLM(chatFn, llmParams, null);

    // Create a mapped version of the context content for easier lookups
    const contentByIndex = {};
    context.content.forEach((c, idx) => {
        contentByIndex[idx] = c;
    });
    
    // Create a clean representation of the document for the LLM
    const documentLines = context.content.map((item, idx) => 
        `[${idx}] ${item.content.replace(/\n/g, ' ')}`
    ).join('\n');
    
    // Create a clear, structured prompt for the LLM
    const updatedBody = {
        ...chatBody,
        messages: [{
            role: 'system',
            content: `You are a precise document analyzer specializing in finding only the most relevant information. 
Your task is to identify the exact indexes of document fragments that would help answer a user's question.

IMPORTANT INSTRUCTIONS:
1. The document fragments are labeled with indexes like [0], [1], [2], etc.
2. Only select fragments that contain information DIRECTLY relevant to answering the user's question.
3. Output ONLY a JSON array of index numbers (as integers), nothing else.
4. If no fragments are relevant, return an empty array: []
5. DO NOT explain your reasoning - provide ONLY the array of indexes.

Example outputs:
- For relevant fragments: [3, 4, 7, 12]
- For no relevant fragments: []`
          },{
            role: 'user',
            content: `USER QUESTION:
${userMessage}

DOCUMENT FRAGMENTS (with indexes):
${documentLines}

Return ONLY the indexes of fragments that contain information directly relevant to answering the question.
Expected format: [0, 1, 5] or [] if nothing is relevant.`
          }],
        imageSources: [],
        model: model.id,
        max_tokens: 300, // Lower token limit, we only need the array
        options: {
            ...chatBody.options,
            model,
            skipRag: true,
            ragOnly: false,
            skipDocumentCache: true,
            prompt: `Return ONLY a list with indexes: ex. [0, 1, 5] or [] if nothing is relevant.`
        }
    };

    // Use a simpler prompt for data
    const extraction = await llm.promptForData(
        updatedBody,
        [],
        '', // prompt already in messages
        {
            "relevantIndexes": "Array of integer indexes indicating relevant document fragments",
        },
        null,
        (r) => {
            // Ensure we get a valid array of numbers
            if (!r.relevantIndexes) {
                return null;
            }
            try {
                const indexes = JSON.parse(r.relevantIndexes);
                return indexes.filter(idx => typeof idx === 'number' && idx >= 0 && idx < context.content.length);
            } catch (e) {
                logger.error("Error parsing relevant indexes:", e);
                return null;
            }
        },
        2 // Fewer retries for faster response
    );
 
    // Handle case where no relevant content was found
    if (!extraction.relevantIndexes) {
        logger.debug("Error extracting relevant content in llm call, dumping entire document context as a fallback...");
        return context; // Return unchanged context if nothing relevant found
    } else if  (extraction.relevantIndexes.length === 0) {
        logger.debug("No datasource content was relevant to the query");
        return null;
    }
    const indexes = JSON.parse(extraction.relevantIndexes);
    // Filter the content to only include relevant items
    const filteredContent = indexes.map(idx => contentByIndex[idx]).filter(Boolean);

    // Create a new context with only the relevant content
    const filteredContext = {
        ...context,
        content: filteredContent,
        originalContent: context.content,
        totalTokens: filteredContent.reduce((acc, item) => acc + item.tokens, 0)
    };
    
    // Pass the filtered content through the standard formatting/chunking process
    return filteredContext;

}

/**
 * Combines content items that have consecutive/neighboring locations into single items
 * to reduce the number of items in the list while preserving all location information.
 * 
 * @param {Array} contentArray - Array of content objects with content, location properties
 * @returns {Array} - Array of combined content objects with locations array
 */
export const combineNeighboringLocations = (contentArray) => {
    if (!contentArray || contentArray.length === 0) return [];

    // Helper function to get a sortable key from location
    const getLocationKey = (location) => {
        if (!location || typeof location !== 'object') {
            return { primary: 0, secondary: 0, type: 'single', format: 'fallback' };
        }

        // Word documents: section + paragraph (hierarchical)
        if (location.section_number !== undefined && location.paragraph_number !== undefined) {
            return { 
                primary: location.section_number, 
                secondary: location.paragraph_number,
                tertiary: location.section_title || '',
                type: 'hierarchical',
                format: 'section-paragraph'
            };
        }
        
        // Excel: sheet + row (hierarchical)
        if (location.sheet_number !== undefined && location.row_number !== undefined) {
            return { 
                primary: location.sheet_number, 
                secondary: location.row_number,
                tertiary: location.sheet_name || '',
                type: 'hierarchical',
                format: 'sheet-row'
            };
        }

        // Single dimension numeric locations
        if (location.line_number !== undefined) return { primary: location.line_number, type: 'single', format: 'line' };
        if (location.page_number !== undefined) return { primary: location.page_number, type: 'single', format: 'page' };
        if (location.slide_number !== undefined) return { primary: location.slide_number, type: 'single', format: 'slide' };
        if (location.row_number !== undefined) return { primary: location.row_number, type: 'single', format: 'row' };
        if (location.paragraph_number !== undefined) return { primary: location.paragraph_number, type: 'single', format: 'paragraph' };
        if (location.section_number !== undefined) return { primary: location.section_number, type: 'single', format: 'section' };
        if (location.sheet_number !== undefined) return { primary: location.sheet_number, type: 'single', format: 'sheet' };
        
        // String-based location keys (no consecutive grouping possible)
        if (location.section_title !== undefined) return { primary: location.section_title, type: 'string', format: 'section_title' };
        if (location.sheet_name !== undefined) return { primary: location.sheet_name, type: 'string', format: 'sheet_name' };
        
        return { primary: 0, type: 'single', format: 'fallback' };
    };

    // Helper function to check if two locations are consecutive
    const areConsecutive = (loc1, loc2) => {
        const key1 = getLocationKey(loc1);
        const key2 = getLocationKey(loc2);
        
        // Must be same type and format to be consecutive
        if (key1.type !== key2.type || key1.format !== key2.format) {
            return false;
        }
        
        if (key1.type === 'hierarchical') {
            // Must be in same primary location (same section/sheet)
            if (key1.primary !== key2.primary) return false;
            
            // Check if secondary keys are consecutive
            return Math.abs(key1.secondary - key2.secondary) <= 1;
        }
        
        if (key1.type === 'single') {
            // Only check consecutive for numeric types
            return Math.abs(key1.primary - key2.primary) <= 1;
        }
        
        if (key1.type === 'string') {
            // For strings, they are only "consecutive" if they're identical
            return key1.primary === key2.primary;
        }
        
        return false;
    };

    // Helper function to create a composite sort key
    const createSortKey = (location) => {
        const key = getLocationKey(location);
        
        if (key.type === 'hierarchical') {
            // Create a composite key for hierarchical sorting
            return `${key.format}_${String(key.primary).padStart(10, '0')}_${String(key.secondary).padStart(10, '0')}`;
        }
        
        if (key.type === 'single') {
            return `${key.format}_${String(key.primary).padStart(10, '0')}`;
        }
        
        if (key.type === 'string') {
            return `${key.format}_${key.primary}`;
        }
        
        return `${key.format}_${String(key.primary).padStart(10, '0')}`;
    };

    // Sort content by location key with proper hierarchical ordering
    const sortedContent = [...contentArray].sort((a, b) => {
        const sortKeyA = createSortKey(a.location);
        const sortKeyB = createSortKey(b.location);
        
        return sortKeyA.localeCompare(sortKeyB);
    });

    const combined = [];
    let currentGroup = [sortedContent[0]];

    for (let i = 1; i < sortedContent.length; i++) {
        const current = sortedContent[i];
        const previous = currentGroup[currentGroup.length - 1];

        if (areConsecutive(previous.location, current.location)) {
            // Add to current group
            currentGroup.push(current);
        } else {
            // Start new group - first combine the current group
            if (currentGroup.length === 1) {
                // Single item, keep original structure but wrap location in array
                combined.push({
                    content: currentGroup[0].content,
                    locations: [currentGroup[0].location]
                });
            } else {
                // Multiple items, combine them
                const combinedContent = currentGroup.map(item => item.content).join(' ');
                const combinedLocations = currentGroup.map(item => item.location);
                combined.push({
                    content: combinedContent,
                    locations: combinedLocations
                });
            }
            
            // Start new group with current item
            currentGroup = [current];
        }
    }

    // Handle the last group
    if (currentGroup.length === 1) {
        combined.push({
            content: currentGroup[0].content,
            locations: [currentGroup[0].location]
        });
    } else {
        const combinedContent = currentGroup.map(item => item.content).join(' ');
        const combinedLocations = currentGroup.map(item => item.location);
        combined.push({
            content: combinedContent,
            locations: combinedLocations
        });
    }

    return combined;
};