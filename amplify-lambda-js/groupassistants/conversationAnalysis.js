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

async function writeToGroupAssistantConversations(conversationId, assistantId, assistantName, modelUsed, numberPrompts, user, employeeType, entryPoint, s3Location, category, systemRating) {
    const params = {
        TableName: process.env.GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE,
        Item: {
            conversationId: conversationId,
            assistantId: assistantId,
            assistantName: assistantName,
            modelUsed: modelUsed,
            numberPrompts: numberPrompts,
            user: user,
            employeeType: employeeType,
            entryPoint: entryPoint,
            s3Location: s3Location,
            timestamp: new Date().toISOString(),
            category: category,
            systemRating: systemRating
        }
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

export async function analyzeAndRecordGroupAssistantConversation(chatRequest, llmResponse, user) {
    const data = chatRequest.options;
    const conversationId = data.conversationId;
    const assistantId = data.assistantId;
    const assistantName = data.assistantName;
    const modelUsed = data.model.id;
    const advancedModel = data.advancedModel;
    const numberPrompts = data.numPrompts;
    const employeeType = data.groupType;
    const entryPoint = data.source || "Amplify";
    const categories = data.analysisCategories;

    const analysisSchema = defaultAnalysisSchema;
    const hasCategoriess = categories.length > 0;
    if (hasCategoriess) {
        analysisSchema.properties.category.enum = categories
    }
    
    let userEmail = user;
    if (data.source) { // save user email from wordpress
        userEmail = data.user;
    }

    const userPrompt = chatRequest.messages[chatRequest.messages.length - 1].content.substring(61);

    const content = `User Prompt:\n${userPrompt}\nAI Response:\n${llmResponse}\n`;
    // console.log(content);

    const s3Location = await uploadToS3(assistantId, conversationId, content);
    // const s3Location = "tmp";

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

    const analysisPrompt = `Analyze the following conversation and determine its ${hasCategoriess ? "category and " : ""}system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide ${hasCategoriess ? "a category from the predefined list, " : ""}a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`; // , and your reasoning for both

    const updatedChatBody = {
        messages: [
            {
                role: "system",
                content: `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to ${hasCategoriess ? "categorize the conversation and " : ""}rate the quality of the AI response.` // , and provide reasoning for your decisions
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

        const { category, systemRating } = analysis; // reasoning

        await writeToGroupAssistantConversations(
            conversationId, assistantId, assistantName, modelUsed,
            numberPrompts, userEmail, employeeType, entryPoint,
            s3Location, category, systemRating
        );
    } catch (error) {
        logger.debug('Error analyzing or recording conversation:', error);
    }
}
