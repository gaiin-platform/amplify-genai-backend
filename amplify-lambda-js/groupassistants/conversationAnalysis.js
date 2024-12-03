import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { getLogger } from "../common/logging.js";
import { getDefaultLLM } from "../common/llm.js";
import { StreamResultCollector } from "../common/streams.js";
import { transform as fnTransformer } from "../common/chat/events/openaifn.js";
import { Models } from "../models/models.js";
import { createHash } from 'crypto';

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

const analysisSchema = {
    type: "object",
    properties: {
        category: {
            type: "string",
            enum: [
                "Job Application", "Benefits", "Benefits Appeal", "Compensation", "Retirement Plan",
                "Retiring", "Employee Immigration Services", "Employee Records", "International Tax office",
                "Payroll", "Employee Relations", "Engagement Consultant Support", "Leave", "Workers Comp",
                "Employment Verification", "Voyage (orientation new hire)", "Public Service Loan Forgiveness",
                "Conference Room Reservations", "I-9", "Onboarding", "Lyra", "Employee Critical support",
                "Employee Affinity Group", "Educational Resources", "Virgin Pulse", "Wellbeing Champion Program",
                "Mental Health Workshops", "Critical Incident/Grief Support Services"
            ],
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
    const conversationId = chatRequest.options.conversationId;
    const assistantId = chatRequest.options.assistantId;
    const assistantName = chatRequest.options.assistantName;
    const modelUsed = chatRequest.options.model.id;
    const numberPrompts = chatRequest.options.numPrompts;
    const employeeType = chatRequest.options.groupType;
    const entryPoint = chatRequest.options.source || "Amplify";
    
    let userEmail = user;
    if (chatRequest.options.source) { // save user email from wordpress
        userEmail = chatRequest.options.user;
    }

    const userPrompt = chatRequest.messages[chatRequest.messages.length - 1].content.substring(61);

    const content = `User Prompt:\n${userPrompt}\nAI Response:\n${llmResponse}\n`;
    // console.log(content);

    const s3Location = await uploadToS3(assistantId, conversationId, content);
    // const s3Location = "tmp";

    const resultCollector = new StreamResultCollector();
    resultCollector.addTransformer(fnTransformer);

    const model = Models["gpt-4o"];

    const llm = await getDefaultLLM(model, resultCollector, user);

    const analysisPrompt = `Analyze the following conversation and determine its category and system rating:
Prompt: ${userPrompt}
AI Response: ${llmResponse}
Provide a category from the predefined list, a system rating (1-5) based on the AI response quality, relevance, and effectiveness.`; // , and your reasoning for both

    const updatedChatBody = {
        messages: [
            {
                role: "system",
                content: `You are an AI assistant tasked with analyzing conversations. You will be given a user prompt and an AI response. Your job is to categorize the conversation and rate the quality of the AI response.` // , and provide reasoning for your decisions
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
