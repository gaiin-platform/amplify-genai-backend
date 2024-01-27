import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import {getLogger} from "../common/logging.js";

const logger = getLogger("datasources");

const client = new S3Client();

export const getFileText = async (key) => {

    const textKey = key.trim()+".content.json";
    const bucket = process.env.S3_FILE_TEXT_BUCKET_NAME;
    logger.debug("Fetching file from S3",{bucket:bucket, key:textKey});

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
        logger.error(error, {bucket:bucket, key:textKey});
        return null;
    }
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
    if(options && options.chunking && options.chunking.maxItemsPerChunk) {
        maxItemsPerChunk = options.chunking.maxItemsPerChunk;
    }

    return (dataSource, {currentChunk="", currentTokenCount=0, chunks=[], itemCount=0},
            formattedSourceName,
            formattedContent,
            contentTokenCount) => {
        if ((currentTokenCount + contentTokenCount > maxTokens) || (itemCount + 1 > maxItemsPerChunk)) {
            // If the current chunk is too big, push it to the chunks array and start a new one
            if(currentChunk.length > 0) {
                chunks.push({id: dataSource.id + "?chunk="+chunks.length, context: formattedSourceName + currentChunk, tokens: currentTokenCount});
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

    if(content && content.content && content.content.length > 0) {
        const firstLocation = content.content[0].location ? content.content[0].location : null;

        let contentFormatter;
        if (firstLocation && firstLocation.slide) {
            logger.debug("Formatting data from: " + dataSource.id + " as slides");
            contentFormatter = c => 'File: '+content.name+' Slide: ' + c.location.slide + '\n--------------\n' + c.content;
        } else if (firstLocation && firstLocation.page) {
            logger.debug("Formatting data from: " + dataSource.id + " as pages");
            contentFormatter = c => 'File: '+content.name+' Page: ' + c.location.page + '\n--------------\n' + c.content;
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

        const aggregator = getChunkAggregator(maxTokens,options);
        const formattedSourceName = "Source:"+content.name+" Type:"+dataSource.type+"\n-------------\n";

        for (const part of content.content) {
            const formattedContent = contentFormatter(part);
            const contentTokenCount = tokenCounter(formattedContent);
            state = aggregator(dataSource, state, formattedSourceName, formattedContent, contentTokenCount);
        }

        // Add the last chunk if it has content
        if (state.currentChunk) {
            chunks.push({ id: dataSource.id+"?chunk="+chunks.length, context: formattedSourceName + state.currentChunk, tokens: state.currentTokenCount });
        }

        return chunks;

    } else {
        return formatAndChunkDataSource(tokenCounter, dataSource, {content:[{content:content, location:{}}]}, maxTokens);
    }
}


export const getContent = async (dataSource) => {
    const sourceType = extractProtocol(dataSource.id);

    logger.debug("Fetching data from: "+dataSource.id+" ("+sourceType+")");

    if (sourceType === 's3://') {

        logger.debug("Fetching data from S3");
        const result = await getFileText(dataSource.id.slice(sourceType.length));
        logger.debug("Fetched data from S3: " +(result != null));

        if(result == null) {
            throw new Error("Could not fetch data from S3");
        }

        return result;
    }
    else if(sourceType === 'obj://') {
        logger.debug("Fetching data from object");

        const sourceKey = extractKey(dataSource.id);

        const contents = Object.entries(dataSource.content)
            .filter(([k,v]) => !k.startsWith("__tokens_"))
            .map(([k,v])=>{
                return {
                    "content": v,
                    "tokens": dataSource.content["__tokens_"+k] || 1000,
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
    else {
        return [dataSource];
    }
}

export const getContexts = async (tokenCounter, dataSource, maxTokens, options) => {
    const sourceType = extractProtocol(dataSource.id);

    logger.debug("Get contexts with options", options);

    logger.debug("Fetching data from: "+dataSource.id+" ("+sourceType+")");


    const result = await getContent(dataSource);
    return formatAndChunkDataSource(tokenCounter, dataSource, result, maxTokens, options);
}