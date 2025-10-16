import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";
import { getLogger } from "../common/logging.js";
import { StreamResultCollector } from "../common/streams.js";
import { createHash } from 'crypto';
import { promptLiteLLMForData } from "../litellm/litellmClient.js";

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
        console.error(`Error uploading to S3: ${error}`);
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

    try {
        await docClient.send(new PutCommand(params));
        logger.debug(`Successfully wrote conversation data to DynamoDB for conversationId: ${conversationId}`);
    } catch (error) {
        logger.debug(`Error writing to DynamoDB: ${error}`);
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
    const user = account.user
    const data = chatRequest.options;
    const conversationId = data.conversationId;
    const assistantId = data.assistantId;
    const assistantName = data.assistantName;
    const modelUsed = data.model.id;
    const advancedModel = data.advancedModel;
    const numberPrompts = data.numberPrompts || 0; // Use the numberPrompts from options, default to 0 if not set
    console.log(`Received numberPrompts in conversation analysis: ${numberPrompts} (from options: ${JSON.stringify(data.numberPrompts)})`);
    const employeeType = data.groupType;
    const entryPoint = data.source || "Amplify";

    // Only get categories if category analysis is enabled
    const categories = performCategoryAnalysis ? (data.analysisCategories || []) : [];
    const hasCategories = categories.length > 0;

    // Create a modified schema based on whether category analysis is enabled
    let analysisSchema;
    if (performCategoryAnalysis) {
        analysisSchema = { ...defaultAnalysisSchema };
        if (hasCategories) {
            analysisSchema.properties.category.enum = categories;
        }
    } else {
        // If category analysis is disabled, remove the category field from the schema
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

    const s3Location = await uploadToS3(assistantId, conversationId, content);

    if (performCategoryAnalysis) {
        logger.debug("Peforming AI Analysis on conversation");

        const model = advancedModel;

        const analysisPrompt = performCategoryAnalysis
            ? `Analyze the following conversation and determine its ${hasCategories ? "category and " : ""}system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
${hasCategories ? `Available categories: ${categories.join(', ')}
Choose the most appropriate category from this list. ` : ""}Provide ${hasCategories ? "a category from the available categories and " : ""}a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`
            : `Analyze the following conversation and determine its system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`;

        const updatedChatBody = {
            messages: [
                {
                    role: "system",
                    content: performCategoryAnalysis
                        ? `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to ${hasCategories ? "categorize the conversation and " : ""}rate the quality of the AI response.${hasCategories ? ` When categorizing, you must choose from the provided list of categories only.` : ""}`
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

        try {
            const analysisResult = await promptLiteLLMForData(
                updatedChatBody.messages,
                model,
                '', // prompt already in messages
                performCategoryAnalysis ? {
                    "category": "String category from the predefined list",
                    "systemRating": "Integer rating from 1-5 based on AI response quality"
                } : {
                    "systemRating": "Integer rating from 1-5 based on AI response quality"
                },
                account, // 🚨 CRITICAL FIX: Add account for usage tracking
                `analysis_${conversationId}_${Date.now()}`, // 🚨 CRITICAL FIX: Generate requestId for usage tracking
                {
                    maxTokens: 300,
                    temperature: 0.1
                }
            );
            
            // Validate and parse the response
            let analysis = null;
            if (analysisResult && analysisResult.systemRating) {
                try {
                    const systemRating = parseInt(analysisResult.systemRating);
                    if (!isNaN(systemRating) && systemRating >= 1 && systemRating <= 5) {
                        analysis = { systemRating };
                        
                        // Only validate category if category analysis is enabled
                        if (performCategoryAnalysis) {
                            if (analysisResult.category) {
                                // Validate category against allowed values if we have them
                                if (!hasCategories || categories.includes(analysisResult.category)) {
                                    analysis.category = analysisResult.category;
                                } else {
                                    logger.error("Invalid category:", analysisResult.category);
                                }
                            } else {
                                logger.error("Missing category in analysis result");
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
                logger.error("Error analyzing conversation, skipping analysis..");
                return;
            }

            // Extract relevant values from analysis
            const systemRating = analysis.systemRating;
            // Only use category if category analysis is enabled
            const category = performCategoryAnalysis ? (analysis.category || null) : null;

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
        } catch (error) {
            logger.debug('Error analyzing or recording conversation:', error);
        }
    }
    else {
        logger.debug("Skipping AI Analysis on conversation");
        try {
            await writeToGroupAssistantConversations(
                conversationId,
                assistantId,
                assistantName,
                modelUsed,
                numberPrompts,
                userEmail,
                s3Location
            );
        } catch (error) {
            logger.debug('Error analyzing or recording conversation:', error);
        }
    }
}

// ✅ CRITICAL SPEED OPTIMIZATION: Async Queue Functions

/**
 * ✅ SPEED OPTIMIZATION: Queue conversation analysis instead of processing synchronously
 */
export async function queueConversationAnalysis(chatRequest, llmResponse, account, performCategoryAnalysis = true) {
    try {
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
    logger.debug('Processing conversation analysis from SQS', { recordCount: event.Records?.length });

    const results = [];
    
    for (const record of event.Records || []) {
        try {
            const messageBody = JSON.parse(record.body);
            
            const {
                chatRequest,
                llmResponse, 
                account,
                performCategoryAnalysis = true
            } = messageBody;
            
            logger.debug('Processing conversation analysis for request', {
                conversationId: chatRequest?.options?.conversationId,
                assistantId: chatRequest?.options?.assistantId,
                user: account?.user
            });
            
            await analyzeAndRecordGroupAssistantConversation(
                chatRequest,
                llmResponse,
                account,
                performCategoryAnalysis
            );
            
            results.push({
                messageId: record.messageId,
                status: 'success'
            });
            
            logger.debug('Successfully processed conversation analysis', {
                messageId: record.messageId,
                conversationId: chatRequest?.options?.conversationId
            });
            
        } catch (error) {
            logger.error('Failed to process conversation analysis', {
                messageId: record.messageId,
                error: error.message,
                stack: error.stack
            });
            
            results.push({
                messageId: record.messageId,
                status: 'error',
                error: error.message
            });
            
            throw error;
        }
    }
    
    return {
        statusCode: 200,
        body: JSON.stringify({
            processed: results.length,
            results
        })
    };
};

logger.info("Conversation analysis with async queue support initialized - major speed optimization active");
