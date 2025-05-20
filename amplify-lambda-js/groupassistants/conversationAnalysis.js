import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { getLogger } from "../common/logging.js";
import { getDefaultLLM } from "../common/llm.js";
import { StreamResultCollector } from "../common/streams.js";
import { transform as fnTransformer } from "../common/chat/events/openaifn.js";
import { createHash } from 'crypto';
import { getChatFn } from "../common/params.js";

const logger = getLogger("conversationAnalysis");

const dynamodbClient = new DynamoDBClient({ region: "us-east-1" });
const docClient = DynamoDBDocumentClient.from(dynamodbClient);
const s3Client = new S3Client({ region: "us-east-1" });

function calculateMD5(content) {
    return createHash('md5').update(content).digest('base64');
}

async function uploadToS3(assistantId, conversationId, content) {
    const bucketName = process.env.S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME;
    const key = `${assistantId}/${conversationId}.txt`;

    try {
        // Check if the file already exists
        try {
            const existingObject = await s3Client.send(new GetObjectCommand({
                Bucket: bucketName,
                Key: key,
            }));

            // If file exists, append new content
            const existingContent = await existingObject.Body.transformToString();
            content = existingContent + '\n' + content;
        } catch (error) {
            // If file doesn't exist, we'll create a new one
            if (error.name !== 'NoSuchKey') {
                throw error;
            }
        }

        await s3Client.send(new PutObjectCommand({
            Bucket: bucketName,
            Key: key,
            Body: content,
            ContentType: 'text/plain',
            ContentMD5: calculateMD5(content)
        }));

        logger.debug(`Successfully uploaded conversation to S3: ${key}`);
        return `s3://${bucketName}/${key}`;
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

export async function analyzeAndRecordGroupAssistantConversation(chatRequest, llmResponse, user, performCategoryAnalysis = true) {
    const data = chatRequest.options;
    const conversationId = data.conversationId;
    const assistantId = data.assistantId;
    const assistantName = data.assistantName;
    const modelUsed = data.model.id;
    const advancedModel = data.advancedModel;
    const numberPrompts = data.numberPrompts || 0; // Use the numberPrompts from options, default to 0 if not set
    console.log(`!!!!!! Received numberPrompts in conversation analysis: ${numberPrompts} (from options: ${JSON.stringify(data.numberPrompts)})`);
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

        const resultCollector = new StreamResultCollector();
        resultCollector.addTransformer(fnTransformer);

        const model = advancedModel;

        // set up llm 
        let llm = await getDefaultLLM(model, resultCollector, user);
        //we need to ensure the chatFn is adjusted according to the model 
        const chatFn = async (body, writable, context) => {
            return await getChatFn(model, body, writable, context);
        }
        llm = llm.clone(chatFn);

        const analysisPrompt = performCategoryAnalysis
            ? `Analyze the following conversation and determine its ${hasCategories ? "category and " : ""}system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide ${hasCategories ? "a category from the predefined list, " : ""}a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`
            : `Analyze the following conversation and determine its system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`;

        const updatedChatBody = {
            messages: [
                {
                    role: "system",
                    content: performCategoryAnalysis
                        ? `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to ${hasCategories ? "categorize the conversation and " : ""}rate the quality of the AI response.`
                        : `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to rate the quality of the AI response.`
                },
                {
                    role: "user",
                    content: analysisPrompt
                }
            ],
            options: {
                model: model
            }
        };

        try {
            const analysis = await llm.promptForJson(
                updatedChatBody,
                analysisSchema,
                [],
                resultCollector
            );

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
