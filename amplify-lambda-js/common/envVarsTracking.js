//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { DynamoDBClient, GetItemCommand, PutItemCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";

const dynamodbClient = new DynamoDBClient();

// Container-level cache to avoid redundant tracking and resolution
const trackedConfigs = new Set();
const resolvedEnvVars = new Map();

// AWS Operation Enums (JavaScript equivalent of PyCommon enums)
// Each operation has a 'value' property for enum validation
export const DynamoDBOperation = {
    GET_ITEM: { value: "GET_ITEM" },
    PUT_ITEM: { value: "PUT_ITEM" }, 
    UPDATE_ITEM: { value: "UPDATE_ITEM" },
    DELETE_ITEM: { value: "DELETE_ITEM" },
    QUERY: { value: "QUERY" },
    SCAN: { value: "SCAN" }
};

export const S3Operation = {
    GET_OBJECT: { value: "GET_OBJECT" },
    PUT_OBJECT: { value: "PUT_OBJECT" },
    DELETE_OBJECT: { value: "DELETE_OBJECT" },
    LIST_BUCKET: { value: "LIST_BUCKET" }
};

export const SQSOperation = {
    SEND_MESSAGE: { value: "SEND_MESSAGE" },
    RECEIVE_MESSAGE: { value: "RECEIVE_MESSAGE" },
    DELETE_MESSAGE: { value: "DELETE_MESSAGE" },
    GET_QUEUE_ATTRIBUTES: { value: "GET_QUEUE_ATTRIBUTES" }
};

export const SecretsManagerOperation = {
    GET_SECRET_VALUE: { value: "GET_SECRET_VALUE" },
    PUT_SECRET_VALUE: { value: "PUT_SECRET_VALUE" }
};

export const BedrockOperation = {
    INVOKE_MODEL: { value: "INVOKE_MODEL" },
    INVOKE_MODEL_WITH_RESPONSE_STREAM: { value: "INVOKE_MODEL_WITH_RESPONSE_STREAM" },
    INVOKE_GUARDRAIL: { value: "INVOKE_GUARDRAIL" }
};

export const SSMOperation = {
    GET_PARAMETER: { value: "GET_PARAMETER" },
    PUT_PARAMETER: { value: "PUT_PARAMETER" }
};

export const LambdaOperation = {
    INVOKE_FUNCTION: { value: "INVOKE_FUNCTION" },
    GET_FUNCTION: { value: "GET_FUNCTION" }
};

export const CognitoOperation = {
    ADMIN_GET_USER: { value: "ADMIN_GET_USER" },
    ADMIN_CREATE_USER: { value: "ADMIN_CREATE_USER" }
};

/**
 * Environment Variable Tracker - JavaScript equivalent of PyCommon's EnvVarTracker
 */
class EnvVarTracker {
    constructor() {
        this.stage = process.env.STAGE || "dev";
        this.serviceName = process.env.SERVICE_NAME || "unknown";
        this.trackingTable = process.env.ENV_VARS_TRACKING_TABLE;
        this.region = process.env.AWS_REGION || "us-east-1";
        
        this.trackingEnabled = !!this.trackingTable;
    }

    /**
     * Resolve environment variable - JavaScript simplified version
     * 
     * Note: JavaScript Lambda uses serverless.yml Parameter Store references
     * like ${ssm:/amplify/${sls:stage}/...} so all variables should already
     * be resolved in the Lambda environment. No runtime Parameter Store
     * fallback needed.
     */
    async resolveEnvVar(varName) {
        // Lambda environment (set by serverless.yml Parameter Store references)
        const value = process.env[varName];
        if (value) {
            return value.trim();
        }

        // Variable not found
        throw new Error(
            `Environment variable '${varName}' not found in Lambda environment. ` +
            `Ensure it's defined in serverless.yml with Parameter Store reference.`
        );
    }

    /**
     * Track environment variable usage in DynamoDB - matches PyCommon schema exactly
     */
    async trackEnvVar(varName, operations = [], resolvedValue = null) {
        if (!this.trackingEnabled) {
            return;
        }

        try {
            const serviceVarKey = `${this.serviceName}#${varName}`;
            // Match PyCommon schema - calculated path for consistency
            const parameterPath = `/amplify/${this.stage}/${this.serviceName}/${varName}`;

            // Use provided value or get current value
            if (resolvedValue === null) {
                resolvedValue = (process.env[varName] || "").trim();
            }

            // Convert operations to strings if they're enum values (match Python logic)
            const operationStrings = operations.map(op => 
                op && typeof op === 'object' && op.value ? op.value : String(op)
            );

            // Check for version tracking
            const version = process.env.VERSION ? process.env.VERSION.trim() : undefined;
            const timestamp = new Date().toISOString();

            // Try to get existing record and merge operations
            try {
                const getCommand = new GetItemCommand({
                    TableName: this.trackingTable,
                    Key: marshall({ service_var_key: serviceVarKey })
                });

                const response = await dynamodbClient.send(getCommand);
                if (response.Item) {
                    const existingItem = unmarshall(response.Item);
                    const existingOperations = new Set(existingItem.operations || []);
                    const newOperations = new Set(operationStrings);
                    const existingVersion = existingItem.version;

                    // Check if we need to update operations or version
                    const operationsToAdd = [...newOperations].filter(op => !existingOperations.has(op));
                    const versionChanged = version !== undefined && version !== existingVersion;
                    const versionAdded = version !== undefined && existingVersion === undefined;

                    if (operationsToAdd.length > 0 || versionChanged || versionAdded) {
                        // Merge operations (existing + new)
                        const mergedOperations = [...new Set([...existingOperations, ...newOperations])];

                        // Build update expression dynamically
                        const updateExpressionParts = ["SET operations = :operations"];
                        const expressionAttributeValues = { ":operations": mergedOperations };

                        // Add version to update if it exists
                        if (version !== undefined) {
                            updateExpressionParts.push("version = :version");
                            expressionAttributeValues[":version"] = version;
                        }

                        const updateExpression = updateExpressionParts.join(", ");

                        const updateCommand = new UpdateItemCommand({
                            TableName: this.trackingTable,
                            Key: marshall({ service_var_key: serviceVarKey }),
                            UpdateExpression: updateExpression,
                            ExpressionAttributeValues: marshall(expressionAttributeValues),
                            ReturnValues: "ALL_NEW"
                        });

                        const updateResult = await dynamodbClient.send(updateCommand);
                        const versionInfo = version ? `, version: ${version}` : "";
                        console.log(
                            `ENV_VAR_TRACKING: MERGED ${serviceVarKey} - ` +
                            `added ${JSON.stringify(operationsToAdd)} → ` +
                            `now: ${JSON.stringify(unmarshall(updateResult.Attributes).operations)}` +
                            versionInfo
                        );
                    }
                    return;
                }
            } catch (error) {
                // Fall through to create new record if get/update fails
            }

            // Create new tracking record (matches PyCommon schema exactly)
            const recordItem = {
                service_var_key: serviceVarKey,
                service_name: this.serviceName,
                var_name: varName,  // Match PyCommon schema
                resolved_value: resolvedValue,  // Match PyCommon schema  
                parameter_path: parameterPath,  // Match PyCommon schema (calculated for consistency)
                operations: operationStrings,
                first_accessed: timestamp  // Match PyCommon schema
            };

            // Only add version if it exists (match PyCommon logic)
            if (version !== undefined) {
                recordItem.version = version;
            }

            const putCommand = new PutItemCommand({
                TableName: this.trackingTable,
                Item: marshall(recordItem)
            });

            await dynamodbClient.send(putCommand);

            const versionInfo = version ? `, version: ${version}` : "";
            console.log(
                `ENV_VAR_TRACKING: PUT NEW item for ${serviceVarKey} - ` +
                `operations: ${JSON.stringify(operationStrings)}${versionInfo}`
            );

        } catch (error) {
            // Never fail the function for tracking issues (match PyCommon behavior)
            console.warn(`ENV_VAR_TRACKING: Failed to track environment variable ${varName}: ${error.message}`);
        }
    }
}

/**
 * JavaScript equivalent of PyCommon's @required_env_vars decorator
 * 
 * Provides:
 * - Environment variable resolution (Lambda env → Parameter Store → Error)
 * - Usage tracking in DynamoDB with specific AWS operations  
 * - Precise IAM permission documentation for security auditing
 * 
 * @param {Object} envVarsDict - Dictionary mapping env var names to required AWS operations
 * @returns {Function} - Decorator function
 */
export const requiredEnvVars = (envVarsDict) => {
    // Validate environment variable specifications (match PyCommon validation)
    if (!envVarsDict || typeof envVarsDict !== 'object') {
        throw new Error(
            "required_env_vars expects a dictionary mapping env var names to operation lists"
        );
    }

    // Parse environment variables and operations (match PyCommon validation)
    for (const [varName, operations] of Object.entries(envVarsDict)) {
        if (typeof varName !== 'string') {
            throw new Error(`Environment variable name must be a string: ${varName}`);
        }
        if (!Array.isArray(operations)) {
            throw new Error(`Operations must be a list for ${varName}: ${operations}`);
        }
        for (const op of operations) {
            // Check if it's an enum (has value property) - match PyCommon validation
            if (op && typeof op === 'object' && !op.hasOwnProperty('value')) {
                throw new Error(
                    `Invalid operation ${op} for ${varName}. Must use AWS ` +
                    `operation enums (e.g., DynamoDBOperation.GET_ITEM)`
                );
            }
        }
    }

    return (originalFunction) => {
        const wrapper = async (...args) => {
            // Create a unique cache key for this configuration
            const configKey = JSON.stringify(envVarsDict);
            
            const tracker = new EnvVarTracker();

            // Resolve and track all declared environment variables
            for (const [varName, operations] of Object.entries(envVarsDict)) {
                try {
                    // Resolve the environment variable (with Parameter Store fallback)
                    const resolvedValue = await tracker.resolveEnvVar(varName);

                    // Ensure it's available in environment for the function
                    process.env[varName] = resolvedValue;

                    // Track usage in DynamoDB (non-blocking, only once per container)
                    if (!trackedConfigs.has(configKey)) {
                        tracker.trackEnvVar(varName, operations, resolvedValue).catch(error => {
                            console.warn(`ENV_VAR_TRACKING: Background tracking failed for ${varName}: ${error.message}`);
                        });
                    }

                } catch (error) {
                    // Re-raise env variable errors - these should fail the function (match PyCommon)
                    console.error(`Required environment variable '${varName}' not available`);
                    throw error;
                }
            }

            // Mark as tracked for this container lifecycle
            if (!trackedConfigs.has(configKey)) {
                trackedConfigs.add(configKey);
            }

            // Call the original function with all environment variables resolved
            return await originalFunction(...args);
        };

        // Store metadata on function for introspection/documentation generation (match PyCommon)
        wrapper._required_env_vars = envVarsDict;
        wrapper._env_var_operations = {};
        for (const [varName, operations] of Object.entries(envVarsDict)) {
            wrapper._env_var_operations[varName] = operations.map(op => 
                op && typeof op === 'object' && op.value ? op.value : String(op)
            );
        }

        return wrapper;
    };
};

/**
 * Convenience wrapper for Lambda handlers 
 * 
 * Usage:
 * export const handler = withEnvVarsTracking({
 *   "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
 *   "S3_SHARE_BUCKET_NAME": [S3Operation.PUT_OBJECT, S3Operation.GET_OBJECT],
 *   "LLM_ENDPOINTS_SECRETS_NAME_ARN": [SecretsManagerOperation.GET_SECRET_VALUE]
 * }, async (event, context) => {
 *   // Your handler logic here
 * });
 * 
 * Note: Matches PyCommon @required_env_vars decorator exactly:
 * - Environment variable resolution (Lambda env → Parameter Store → Error)  
 * - Usage tracking in DynamoDB with specific AWS operations
 * - Precise IAM permission documentation for security auditing
 * - Version tracking support
 * 
 * @param {Object} envVarsConfig - Environment variables configuration
 * @param {Function} handler - Original Lambda handler function
 * @returns {Function} - Wrapped handler with env var tracking
 */
export const withEnvVarsTracking = (envVarsConfig, handler) => {
    return requiredEnvVars(envVarsConfig)(handler);
};