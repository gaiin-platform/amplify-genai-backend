import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";
import { getLogger } from "../common/logging.js";
import { StreamResultCollector } from "../common/streams.js";
import { createHash } from 'crypto';
import { promptUnifiedLLMForData } from "../llm/UnifiedLLMClient.js";

const logger = getLogger("conversationAnalysis");

const dynamodbClient = new DynamoDBClient({ region: "us-east-1" });
const docClient = DynamoDBDocumentClient.from(dynamodbClient);
const s3Client = new S3Client({ region: "us-east-1" });
const sqsClient = new SQSClient({ region: process.env.DEP_REGION || "us-east-1" });

function calculateMD5(content) {
    return createHash('md5').update(content).digest('base64');
}

async function uploadToS3(assistantId, conversationId, content) {
    const consolidationBucketName = process.env.S3_CONSOLIDATION_BUCKET_NAME;
    const legacyBucketName = process.env.S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME; // Marked for deletion
    const consolidationKey = `agentConversations/${assistantId}/${conversationId}.txt`;
    const legacyKey = `${assistantId}/${conversationId}.txt`;


    try {
        let existingContent = null;
        let foundInConsolidation = false;

        // Check consolidation bucket first for existing content
        try {
            const existingObject = await s3Client.send(new GetObjectCommand({
                Bucket: consolidationBucketName,
                Key: consolidationKey,
            }));
            existingContent = await existingObject.Body.transformToString();
            foundInConsolidation = true;
        } catch (error) {
            if (error.name !== 'NoSuchKey') {
                throw error;
            }
        }

        // If not found in consolidation bucket, check legacy bucket
        if (!foundInConsolidation && legacyBucketName) {
            try {
                const existingObject = await s3Client.send(new GetObjectCommand({
                    Bucket: legacyBucketName,
                    Key: legacyKey,
                }));
                existingContent = await existingObject.Body.transformToString();
            } catch (error) {
                if (error.name !== 'NoSuchKey') {
                    throw error;
                }
            }
        }

        // If existing content found, append new content
        if (existingContent) {
            content = existingContent + '\n' + content;
        }

        // Always upload to consolidation bucket (for new or updated content)
        await s3Client.send(new PutObjectCommand({
            Bucket: consolidationBucketName,
            Key: consolidationKey,
            Body: content,
            ContentType: 'text/plain',
            ContentMD5: calculateMD5(content)
        }));

        logger.debug(`Successfully uploaded conversation to consolidation S3: ${consolidationKey}`);
        
        // Return just the key path (without s3:// prefix) to indicate migrated record
        return consolidationKey;
    } catch (error) {
        logger.error(`Error uploading to S3: ${error}`);
        throw error;
    }
}

async function writeToGroupAssistantConversations(conversationId, assistantId, assistantName, modelUsed, numberPrompts, user, s3Location, options = {}) {
    // Extract optional parameters with defaults as undefined
    const { employeeType, entryPoint, category, systemRating } = options;

    // Build the base Item object with required fields
    const item = {
        conversationId,
        assistantId,
        assistantName,
        modelUsed,
        numberPrompts: numberPrompts || 0, // Ensure numberPrompts is always set, defaulting to 0 if undefined
        user,
        s3Location,
        timestamp: new Date().toISOString()
    };

    // Add optional fields only if they are defined
    if (employeeType !== undefined) item.employeeType = employeeType;
    if (entryPoint !== undefined) item.entryPoint = entryPoint;
    if (category !== undefined) item.category = category;
    if (systemRating !== undefined) item.systemRating = systemRating;

    const params = {
        TableName: process.env.GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE,
        Item: item
    };

    logger.debug('🗃️ Preparing DynamoDB write operation', {
        conversationId,
        tableName: process.env.GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE,
        itemKeys: Object.keys(item),
        hasOptionalFields: {
            employeeType: employeeType !== undefined,
            entryPoint: entryPoint !== undefined,
            category: category !== undefined,
            systemRating: systemRating !== undefined
        }
    });

    try {
        await docClient.send(new PutCommand(params));
        logger.info(`✅ Successfully wrote conversation data to DynamoDB`, {
            conversationId,
            assistantId,
            user,
            category,
            systemRating,
            s3Location
        });
    } catch (error) {
        logger.error(`❌ Error writing to DynamoDB`, {
            conversationId,
            assistantId,
            user,
            error: error.message,
            stack: error.stack,
            tableName: process.env.GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE
        });
        throw error; // Re-throw to allow proper error handling upstream
    }
}

const defaultAnalysisSchema = {
    type: "object",
    properties: {
        category: {
            type: "string",
            enum: [],
            description: "The category of the conversation"
        },
        systemRating: {
            type: "integer",
            minimum: 1,
            maximum: 5,
            description: "System rating of the conversation quality (1-5)"
        }
        // reasoning: {
        //     type: "string",
        //     description: "Explanation for the category and system rating determination"
        // }
    },
    required: ["category", "systemRating"] // , "reasoning"
};

export async function analyzeAndRecordGroupAssistantConversation(chatRequest, llmResponse, account, performCategoryAnalysis = true) {
    logger.info('📊 Starting conversation analysis and recording', {
        conversationId: chatRequest?.options?.conversationId,
        assistantId: chatRequest?.options?.assistantId,
        user: account?.user,
        performCategoryAnalysis,
        llmResponseType: typeof llmResponse,
        llmResponseLength: typeof llmResponse === 'string' ? llmResponse.length : 0
    });

    const user = account.user
    const data = chatRequest.options;
    const conversationId = data.conversationId;
    const assistantId = data.assistantId;
    const assistantName = data.assistantName;
    const modelUsed = data.model.id;
    const advancedModel = data.advancedModel;
    const numberPrompts = data.numberPrompts || 0; // Use the numberPrompts from options, default to 0 if not set
    logger.debug(`📈 Conversation metadata extracted`, {
        conversationId,
        assistantId,
        assistantName,
        modelUsed,
        advancedModel: advancedModel?.id || advancedModel,
        numberPrompts,
        numberPromptsFromOptions: data.numberPrompts,
        user
    });
    const employeeType = data.groupType;
    const entryPoint = data.source || "Amplify";

    // Always get categories from options, regardless of performCategoryAnalysis flag
    const categories = data.analysisCategories || [];
    const hasCategories = categories.length > 0;
    
    logger.debug('📋 Category analysis configuration', {
        conversationId,
        performCategoryAnalysis,
        hasCategories,
        categoriesCount: categories.length,
        categories: hasCategories ? categories : 'none'
    });

    // Create analysis schema - always include system rating, include category only if categories provided
    let analysisSchema;
    if (hasCategories) {
        // Include both category and system rating
        analysisSchema = { ...defaultAnalysisSchema };
        analysisSchema.properties.category.enum = categories;
    } else {
        // Only system rating, no category
        analysisSchema = {
            type: "object",
            properties: {
                systemRating: {
                    type: "integer",
                    minimum: 1,
                    maximum: 5,
                    description: "System rating of the conversation quality (1-5)"
                }
            },
            required: ["systemRating"]
        };
    }

    let userEmail = user;
    if (data.source) { // save user email from wordpress
        userEmail = data.user;
    }

    const userPrompt = chatRequest.messages[chatRequest.messages.length - 1].content;

    const content = `User Prompt:\n${userPrompt}\nAI Response:\n${llmResponse}\n`;
    
    logger.debug('📝 Content prepared for S3 upload', {
        conversationId,
        userPromptLength: userPrompt?.length || 0,
        userPromptPreview: userPrompt?.substring(0, 100) + '...',
        llmResponseLength: typeof llmResponse === 'string' ? llmResponse.length : 0,
        llmResponsePreview: typeof llmResponse === 'string' ? llmResponse.substring(0, 100) + '...' : llmResponse,
        totalContentLength: content.length,
        hasUserPrompt: !!userPrompt,
        hasLlmResponse: !!llmResponse
    });

    logger.info('☁️ Uploading conversation to S3', {
        conversationId,
        assistantId,
        contentLength: content.length
    });

    const s3Location = await uploadToS3(assistantId, conversationId, content);
    
    logger.debug('✅ S3 upload completed', {
        conversationId,
        s3Location,
        contentLength: content.length
    });

    // Always perform AI analysis (system rating + optional category analysis)
    logger.info("🤖 Starting AI Analysis on conversation", {
        conversationId,
        assistantId,
        hasCategories,
        categories: categories.length > 0 ? categories : 'none',
        advancedModel: advancedModel?.id || advancedModel,
        analysisType: hasCategories ? 'rating + category' : 'rating only'
    });

    const model = advancedModel;

    try {
        const analysisPrompt = hasCategories
                ? `Analyze the following conversation and determine its category and system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Available categories: ${categories.join(', ')}
Choose the most appropriate category from this list and provide a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`
                : `Analyze the following conversation and determine its system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`;

        const updatedChatBody = {
            messages: [
                {
                    role: "system",
                    content: hasCategories
                        ? `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to categorize the conversation and rate the quality of the AI response. When categorizing, you must choose from the provided list of categories only.`
                        : `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to rate the quality of the AI response.`
                },
                {
                    role: "user",
                    content: analysisPrompt
                }
            ],
            options: {
                model: model,
                rateLimit: chatRequest.options?.rateLimit,
                ...account
            }
        };

        const analysisResult = await promptUnifiedLLMForData(
                {
                    account,
                    options: {
                        model,
                        requestId: `analysis_${conversationId}_${Date.now()}`
                    }
                },
                updatedChatBody.messages,
                hasCategories ? {
                    type: "object",
                    properties: {
                        category: {
                            type: "string",
                            description: "String category from the predefined list"
                        },
                        systemRating: {
                            type: "integer",
                            minimum: 1,
                            maximum: 5,
                            description: "Integer rating from 1-5 based on AI response quality"
                        }
                    },
                    required: ["category", "systemRating"]
                } : {
                    type: "object",
                    properties: {
                        systemRating: {
                            type: "integer",
                            minimum: 1,
                            maximum: 5,
                            description: "Integer rating from 1-5 based on AI response quality"
                        }
                    },
                    required: ["systemRating"]
                },
                null // No streaming
            );
            
            // Validate and parse the response
            let analysis = null;
            if (analysisResult && analysisResult.systemRating) {
                try {
                    const systemRating = parseInt(analysisResult.systemRating);
                    if (!isNaN(systemRating) && systemRating >= 1 && systemRating <= 5) {
                        analysis = { systemRating };
                        
                        // Only validate category if categories were provided
                        if (hasCategories) {
                            if (analysisResult.category) {
                                // Validate category against allowed values
                                if (categories.includes(analysisResult.category)) {
                                    analysis.category = analysisResult.category;
                                } else {
                                    logger.error("❌ Invalid category received from analysis", {
                                        conversationId,
                                        receivedCategory: analysisResult.category,
                                        validCategories: categories
                                    });
                                }
                            } else {
                                logger.error("❌ Missing category in analysis result when categories expected", {
                                    conversationId,
                                    hasCategories,
                                    expectedCategories: categories
                                });
                            }
                        }
                    } else {
                        logger.error("Invalid system rating:", analysisResult.systemRating);
                    }
                } catch (e) {
                    logger.error("Error parsing analysis result:", e);
                }
            }

            // Handle case where analysis failed
            if (!analysis) {
                logger.error("❌ AI Analysis failed, skipping conversation recording", {
                    conversationId,
                    assistantId,
                    user: userEmail
                });
                return;
            }

            // Extract relevant values from analysis
            const systemRating = analysis.systemRating;
            // Only use category if categories were provided and analysis included one
            const category = hasCategories ? (analysis.category || null) : null;
            
            logger.info("✅ AI Analysis completed successfully", {
                conversationId,
                assistantId,
                user: userEmail,
                analysisResults: {
                    systemRating,
                    category: category || 'none'
                },
                performCategoryAnalysis,
                hasCategories
            });

            logger.info('💾 Writing analysis results to DynamoDB', {
                conversationId,
                assistantId,
                assistantName,
                modelUsed,
                numberPrompts,
                userEmail,
                s3Location,
                employeeType,
                entryPoint,
                category,
                systemRating
            });

            await writeToGroupAssistantConversations(
                conversationId,
                assistantId,
                assistantName,
                modelUsed,
                numberPrompts,
                userEmail,
                s3Location,
                {
                    employeeType: employeeType,
                    entryPoint: entryPoint,
                    category: category,
                    systemRating: systemRating
                }
            );
            
            logger.info('🎉 Conversation analysis completed successfully', {
                conversationId,
                assistantId,
                user: userEmail,
                category,
                systemRating,
                s3Location
            });
    } catch (error) {
        logger.error('❌ Error during conversation analysis pipeline', {
            conversationId,
            assistantId,
            user: userEmail,
            error: error.message,
            stack: error.stack
        });
        throw error; // Re-throw to allow proper error handling upstream
    }
}

// ✅ CRITICAL SPEED OPTIMIZATION: Async Queue Functions

/**
 * ✅ SPEED OPTIMIZATION: Queue conversation analysis instead of processing synchronously
 */
export async function queueConversationAnalysis(chatRequest, llmResponse, account, performCategoryAnalysis = true) {
    try {
        logger.info("Conversation analysis with async queueing initiated");

        const queueUrl = process.env.CONVERSATION_ANALYSIS_QUEUE_URL;
        
        if (!queueUrl) {
            logger.error("CONVERSATION_ANALYSIS_QUEUE_URL environment variable not set");
            return false;
        }
        
        const messageBody = {
            chatRequest,
            llmResponse,
            account,
            performCategoryAnalysis,
            queuedAt: new Date().toISOString()
        };
        
        const command = new SendMessageCommand({
            QueueUrl: queueUrl,
            MessageBody: JSON.stringify(messageBody),
            MessageAttributes: {
                conversationId: {
                    DataType: "String",
                    StringValue: chatRequest?.options?.conversationId || "unknown"
                },
                assistantId: {
                    DataType: "String", 
                    StringValue: chatRequest?.options?.assistantId || "unknown"
                },
                user: {
                    DataType: "String",
                    StringValue: account?.user || "unknown"
                }
            }
        });
        
        const result = await sqsClient.send(command);
        
        logger.debug('Successfully queued conversation analysis', {
            messageId: result.MessageId,
            conversationId: chatRequest?.options?.conversationId,
            assistantId: chatRequest?.options?.assistantId,
            user: account?.user
        });
        
        return true;
        
    } catch (error) {
        logger.error('Failed to queue conversation analysis', {
            error: error.message,
            conversationId: chatRequest?.options?.conversationId,
            user: account?.user
        });
        return false;
    }
}

/**
 * ✅ BACKWARD COMPATIBILITY: Fallback to synchronous processing if queue fails
 */
export async function queueConversationAnalysisWithFallback(chatRequest, llmResponse, account, performCategoryAnalysis = true) {
    const queueSuccess = await queueConversationAnalysis(chatRequest, llmResponse, account, performCategoryAnalysis);
    
    if (!queueSuccess) {
        logger.warn('Queue failed, falling back to synchronous analysis', {
            conversationId: chatRequest?.options?.conversationId,
            user: account?.user
        });
        
        try {
            await analyzeAndRecordGroupAssistantConversation(chatRequest, llmResponse, account, performCategoryAnalysis);
        } catch (fallbackError) {
            logger.error('Fallback synchronous analysis also failed', {
                error: fallbackError.message,
                conversationId: chatRequest?.options?.conversationId,
                user: account?.user
            });
        }
    }
}

/**
 * ✅ SQS PROCESSOR: Handler for async conversation analysis processing
 */
export const sqsProcessorHandler = async (event) => {
    const startTime = Date.now();
    logger.info('🚀 Starting SQS conversation analysis processing', { 
        recordCount: event.Records?.length,
        eventSource: event.Records?.[0]?.eventSource,
        timestamp: new Date().toISOString()
    });

    const results = [];
    let processedCount = 0;
    let errorCount = 0;
    
    for (const record of event.Records || []) {
        const recordStartTime = Date.now();
        logger.debug('📥 Processing SQS record', {
            messageId: record.messageId,
            receiptHandle: record.receiptHandle?.substring(0, 20) + '...',
            approximateReceiveCount: record.attributes?.ApproximateReceiveCount,
            sentTimestamp: record.attributes?.SentTimestamp,
            messageSize: record.body?.length
        });
        
        try {
            // Parse message body
            logger.debug('📄 Parsing message body', { messageId: record.messageId });
            const messageBody = JSON.parse(record.body);
            
            const {
                chatRequest,
                llmResponse, 
                account,
                performCategoryAnalysis = true,
                queuedAt
            } = messageBody;
            
            // Validate required data
            logger.debug('🔍 Validating conversation data', {
                messageId: record.messageId,
                hasChatRequest: !!chatRequest,
                hasLlmResponse: !!llmResponse,
                hasAccount: !!account,
                llmResponseLength: typeof llmResponse === 'string' ? llmResponse.length : 0,
                llmResponsePreview: typeof llmResponse === 'string' ? llmResponse.substring(0, 100) + '...' : llmResponse,
                conversationId: chatRequest?.options?.conversationId,
                assistantId: chatRequest?.options?.assistantId,
                assistantName: chatRequest?.options?.assistantName,
                user: account?.user,
                performCategoryAnalysis,
                queuedAt,
                queueDelay: queuedAt ? Date.now() - new Date(queuedAt).getTime() : 'unknown'
            });
            
            // Check for empty responses
            if (!llmResponse || (typeof llmResponse === 'string' && llmResponse.trim().length === 0)) {
                logger.warn('⚠️ Empty LLM response detected', {
                    messageId: record.messageId,
                    conversationId: chatRequest?.options?.conversationId,
                    llmResponse: llmResponse
                });
            }
            
            logger.info('🔄 Starting conversation analysis processing', {
                messageId: record.messageId,
                conversationId: chatRequest?.options?.conversationId,
                assistantId: chatRequest?.options?.assistantId,
                user: account?.user,
                responseLength: typeof llmResponse === 'string' ? llmResponse.length : 0
            });
            
            await analyzeAndRecordGroupAssistantConversation(
                chatRequest,
                llmResponse,
                account,
                performCategoryAnalysis
            );
            
            const recordProcessingTime = Date.now() - recordStartTime;
            processedCount++;
            
            results.push({
                messageId: record.messageId,
                status: 'success',
                processingTimeMs: recordProcessingTime
            });
            
            logger.info('✅ Successfully processed conversation analysis', {
                messageId: record.messageId,
                conversationId: chatRequest?.options?.conversationId,
                user: account?.user,
                processingTimeMs: recordProcessingTime,
                totalProcessed: processedCount
            });
            
        } catch (error) {
            const recordProcessingTime = Date.now() - recordStartTime;
            errorCount++;
            
            logger.error('❌ Failed to process conversation analysis', {
                messageId: record.messageId,
                error: error.message,
                stack: error.stack,
                processingTimeMs: recordProcessingTime,
                totalErrors: errorCount,
                errorType: error.constructor.name
            });
            
            results.push({
                messageId: record.messageId,
                status: 'error',
                error: error.message,
                processingTimeMs: recordProcessingTime
            });
            
            throw error;
        }
    }
    
    const totalProcessingTime = Date.now() - startTime;
    
    logger.info('🏁 SQS conversation analysis processing completed', {
        totalRecords: event.Records?.length || 0,
        successfullyProcessed: processedCount,
        errors: errorCount,
        totalProcessingTimeMs: totalProcessingTime,
        averageProcessingTimeMs: event.Records?.length ? Math.round(totalProcessingTime / event.Records.length) : 0,
        timestamp: new Date().toISOString()
    });
    
    return {
        statusCode: 200,
        body: JSON.stringify({
            processed: results.length,
            successful: processedCount,
            errors: errorCount,
            totalProcessingTimeMs: totalProcessingTime,
            results
        })
    };
};
