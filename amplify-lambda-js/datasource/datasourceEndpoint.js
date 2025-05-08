//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { getLogger } from "../common/logging.js";
import { canReadDataSources } from "../common/permissions.js";
import { 
    getContent, 
    resolveDataSources, 
    extractKey, 
    extractProtocol,
    getDataSourcesInConversation,
    isImage,
    translateUserDataSourcesToHashDataSources
} from "./datasources.js";
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const logger = getLogger("datasourceEndpoint");
const s3Client = new S3Client();

/**
 * Creates signed URLs for an S3 data source.
 * @param {string} key - The S3 key of the data source.
 * @param {string} bucket - The S3 bucket name.
 * @param {boolean} isContentFile - Whether this is a content file (.content.json) or original file
 * @returns {Promise<string>} A signed URL for accessing the data source.
 */
const createSignedUrl = async (key, bucket, isContentFile = true) => {
    try {
        const command = new GetObjectCommand({
            Bucket: bucket,
            Key: key,
        });
        
        // Create a signed URL that expires in 1 hour (3600 seconds)
        const signedUrl = await getSignedUrl(s3Client, command, { expiresIn: 3600 });
        return signedUrl;
    } catch (error) {
        logger.error("Error creating signed URL", error);
        return null;
    }
};

/**
 * Gets the appropriate S3 bucket name for the original file based on file type
 * @param {Object} dataSource - The data source object
 * @returns {string} The S3 bucket name
 */
const getOriginalFileBucket = (dataSource) => {
    // Determine if it's an image file
    const isImageFile = dataSource.type && dataSource.type.startsWith('image/');
    
    // Return the appropriate bucket name
    return isImageFile ? 
        process.env.S3_IMAGE_INPUT_BUCKET_NAME : 
        process.env.S3_RAG_INPUT_BUCKET_NAME;
};

/**
 * Handle an image data source by creating a signed URL for it
 * @param {Object} dataSource - The image data source
 * @param {Object} params - Request parameters
 * @param {Object} chatBody - Chat body from request
 * @param {Object} requestBody - The original request body
 * @returns {Promise<Object>} Processed image data source with signed URL
 */
const handleImageDataSource = async (dataSource, params, chatBody, requestBody) => {
    logger.debug("Processing image data source:", dataSource.id);
    
    const sourceType = extractProtocol(dataSource.id);
    if (sourceType !== 's3://') {
        return {
            ...dataSource,
            error: "Only S3 image sources are supported",
            format: "error"
        };
    }
    
    // After running resolveDataSources, dataSource.id contains the hash version of the key
    // and dataSource.metadata.userDataSourceId contains the original user key (if it was translated)
    const userDataSourceId = dataSource.metadata?.userDataSourceId;
    
    try {
        // Extract the key from the data source ID
        const resolvedKey = extractKey(dataSource.id);
        const originalKey = userDataSourceId ? extractKey(userDataSourceId) : resolvedKey;
        
        // Images are stored in the image bucket
        const imageBucket = process.env.S3_IMAGE_INPUT_BUCKET_NAME;
        
        // Generate a signed URL for the image
        const imageSignedUrl = await createSignedUrl(originalKey, imageBucket);
        
        // Always return a signed URL for images, but don't include content by default
        const result = {
            ...dataSource,
            id: userDataSourceId || dataSource.id, // Original user-facing ID if available
            ref: imageSignedUrl, // Image URL
            format: "signedUrl"
        };
        
        // Only include base64 content if explicitly requested and not using signed URLs option
        const includeContent = requestBody.options && 
                              !requestBody.options.useSignedUrls && 
                              requestBody.options.includeImageContent;
        
        return result;
    } catch (error) {
        logger.error(`Error processing image data source ${dataSource.id}:`, error);
        return {
            ...dataSource,
            error: error.message,
            format: "error"
        };
    }
};

/**
 * Handle a non-image data source by creating signed URLs and retrieving content
 * @param {Object} dataSource - The non-image data source
 * @param {Object} params - Request parameters
 * @param {Object} chatBody - Chat body from request
 * @param {Object} requestBody - The original request body
 * @returns {Promise<Object>} Processed non-image data source with content and signed URLs
 */
const handleNonImageDataSource = async (dataSource, params, chatBody, requestBody) => {
    logger.debug("Processing non-image data source:", dataSource.id);
    
    const sourceType = extractProtocol(dataSource.id);
    const key = extractKey(dataSource.id);
    
    try {
        if (sourceType === 's3://') {
            const contentBucket = process.env.S3_FILE_TEXT_BUCKET_NAME;
            
            // After running resolveDataSources, dataSource.id contains the hash version of the key
            // and dataSource.metadata.userDataSourceId contains the original user key (if it was translated)
            const userDataSourceId = dataSource.metadata?.userDataSourceId;
            
            // Extract the key from the hash-based datasource ID
            const resolvedKey = extractKey(dataSource.id);
            const textKey = resolvedKey.endsWith(".content.json") ? resolvedKey : resolvedKey + ".content.json";
            const originalKey = resolvedKey.endsWith(".content.json") ? resolvedKey.replace(".content.json", "") : resolvedKey;
            
            // Get the appropriate bucket for the original file
            const originalFileBucket = getOriginalFileBucket(dataSource);
            
            if (requestBody.options && requestBody.options.useSignedUrls) {
                // Generate a signed URL for direct access to the hash-based S3 object (content)
                const contentSignedUrl = await createSignedUrl(textKey, contentBucket);

                const originalFileKey = (userDataSourceId) ?
                    userDataSourceId.replace("s3://","") : originalKey;
                // Generate a signed URL for the original file
                const originalFileSignedUrl = await createSignedUrl(originalFileKey, originalFileBucket);

                // Return a response with the original user ID and both signed URLs
                return {
                    ...dataSource, // Preserve all original properties
                    id: userDataSourceId || dataSource.id, // Original user-facing ID if available
                    ref: originalFileSignedUrl, // Original file
                    contentUrl: contentSignedUrl, // Text content
                    format: "signedUrl"
                };
            } else {
                // Get the content using the hash-based ID
                const content = await getContent(chatBody, params, dataSource);
                
                // Generate signed URLs for both content and original file
                const contentSignedUrl = await createSignedUrl(textKey, contentBucket);
                
                const originalFileKey = userDataSourceId ? extractKey(userDataSourceId) : originalKey;
                const originalFileSignedUrl = await createSignedUrl(originalFileKey, originalFileBucket);
                
                // Return both the content and signed URLs
                return {
                    ...dataSource, // Preserve all original properties
                    id: userDataSourceId || dataSource.id, // Original user-facing ID if available
                    content,
                    ref: originalFileSignedUrl, // Original file URL for backward compatibility
                    contentUrl: contentSignedUrl, // Text content URL
                    format: "content"
                };
            }
        } else {
            // For other source types, use the getContent function
            const content = await getContent(chatBody, params, dataSource);
            return {
                ...dataSource, // Preserve all original properties
                content,
                format: "content"
            };
        }
    } catch (error) {
        logger.error(`Error fetching data source ${dataSource.id}`, error);
        return {
            ...dataSource, // Preserve all original properties
            error: error.message,
            format: "error"
        };
    }
};

/**
 * Process a request for datasource content.
 * 
 * @param {Object} params - Request parameters including user information and authentication.
 * @param {Object} requestBody - The request body containing datasource information.
 * @returns {Object} Response with datasource content or references.
 */
export const handleDatasourceRequest = async (params, requestBody) => {
    try {
        logger.info("Processing datasource request", { dataSources: requestBody.dataSources });
        
        if (!params.user) {
            return {
                statusCode: 401,
                body: { error: "Unauthorized" }
            };
        }

        if (!requestBody || !requestBody.dataSources) {
            return {
                statusCode: 400,
                body: { error: "No data sources provided" }
            };
        }

        let dataSources = [...requestBody.dataSources];
        const chatBody = requestBody.chat || { messages: [] };
        
        // If chat messages are provided, extract any data sources from the conversation
        if (chatBody.messages && chatBody.messages.length > 0) {
            const convoDataSources = getDataSourcesInConversation(chatBody, true);
            dataSources = [...dataSources, ...convoDataSources.filter(ds => 
                !dataSources.find(d => d.id === ds.id)
            )];
        }

        // Separate image and non-image data sources
        const imageDataSources = dataSources.filter(ds => isImage(ds));
        const nonImageDataSources = dataSources.filter(ds => !isImage(ds));

        logger.debug("Found image data sources:", imageDataSources.length);
        logger.debug("Found non-image data sources:", nonImageDataSources.length);

        // Resolve aliases (like tag://) to concrete datasources for non-image sources
        try {
            // First resolve data source aliases (like tags)
            // Only process non-image data sources through resolveDataSources
            let resolvedNonImageDataSources = await resolveDataSources(params, chatBody, nonImageDataSources);
            
            // Now explicitly translate user datasources to hash datasources
            // This ensures we have the correct hash keys for S3 objects
            resolvedNonImageDataSources = await translateUserDataSourcesToHashDataSources(params, chatBody, resolvedNonImageDataSources);
            
            logger.debug("Resolved non-image datasources with hash keys:", resolvedNonImageDataSources);

            // Update our working lists with the resolved/translated versions
            nonImageDataSources.splice(0, nonImageDataSources.length, ...resolvedNonImageDataSources);
        } catch (e) {
            logger.error("Error resolving data sources: " + e);
            return {
                statusCode: 401,
                body: { error: "Unauthorized data source access." }
            };
        }
        
        // Process image data sources
        const imageResults = await Promise.all(imageDataSources.map(async (dataSource) => {
            return handleImageDataSource(dataSource, params, chatBody, requestBody);
        }));
        
        // Process each non-image datasource and collect the results
        const nonImageResults = await Promise.all(nonImageDataSources.map(async (dataSource) => {
            return handleNonImageDataSource(dataSource, params, chatBody, requestBody);
        }));

        // Combine image and non-image results
        const combinedResults = [...imageResults, ...nonImageResults];

        return {
            statusCode: 200,
            body: {
                dataSources: combinedResults
            }
        };
    } catch (error) {
        logger.error("Error handling datasource request", error);
        return {
            statusCode: 500,
            body: { error: "Internal server error" }
        };
    }
};