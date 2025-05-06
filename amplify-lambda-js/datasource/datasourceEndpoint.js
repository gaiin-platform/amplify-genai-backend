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
 * Creates a signed URL for an S3 data source.
 * @param {string} key - The S3 key of the data source.
 * @param {string} bucket - The S3 bucket name.
 * @returns {Promise<string>} A signed URL for accessing the data source.
 */
const createSignedUrl = async (key, bucket) => {
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

        // Filter out image data sources which are handled differently
        dataSources = dataSources.filter(ds => !isImage(ds));

        // Resolve aliases (like tag://) to concrete datasources
        try {
            // First resolve data source aliases (like tags)
            dataSources = await resolveDataSources(params, chatBody, dataSources);
            
            // Now explicitly translate user datasources to hash datasources
            // This ensures we have the correct hash keys for S3 objects
            dataSources = await translateUserDataSourcesToHashDataSources(params, chatBody, dataSources);
            
            logger.debug("Resolved datasources with hash keys:", dataSources);
        } catch (e) {
            logger.error("Error resolving data sources: " + e);
            return {
                statusCode: 401,
                body: { error: "Unauthorized data source access." }
            };
        }

        // Process each datasource and collect the results
        const results = await Promise.all(dataSources.map(async (dataSource) => {
            const sourceType = extractProtocol(dataSource.id);
            const key = extractKey(dataSource.id);
            
            try {
                if (sourceType === 's3://') {
                    const bucket = process.env.S3_FILE_TEXT_BUCKET_NAME;
                    
                    // After running resolveDataSources, dataSource.id contains the hash version of the key
                    // and dataSource.metadata.userDataSourceId contains the original user key (if it was translated)
                    const userDataSourceId = dataSource.metadata?.userDataSourceId;
                    
                    // Extract the key from the hash-based datasource ID
                    const resolvedKey = extractKey(dataSource.id);
                    const textKey = resolvedKey.endsWith(".content.json") ? resolvedKey : resolvedKey + ".content.json";
                    
                    if (requestBody.options && requestBody.options.useSignedUrls) {
                        // Generate a signed URL for direct access to the hash-based S3 object
                        const signedUrl = await createSignedUrl(textKey, bucket);
                        
                        // Return a response with the original user ID and the signed URL
                        return {
                            ...dataSource, // Preserve all original properties
                            id: userDataSourceId || dataSource.id, // Original user-facing ID if available
                            ref: signedUrl,
                            format: "signedUrl"
                        };
                    } else {
                        // Get the content using the hash-based ID
                        const content = await getContent(chatBody, params, dataSource);
                        
                        // Also generate a signed URL as an alternative access method
                        const signedUrl = await createSignedUrl(textKey, bucket);
                        
                        // Return both the content and the signed URL
                        return {
                            ...dataSource, // Preserve all original properties
                            id: userDataSourceId || dataSource.id, // Original user-facing ID if available
                            content,
                            ref: signedUrl,
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
        }));

        return {
            statusCode: 200,
            body: {
                dataSources: results
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