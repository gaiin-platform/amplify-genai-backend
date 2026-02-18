/**
 * criticalLogger.js
 * 
 * This module provides a centralized logging mechanism for critical errors across
 * all JavaScript Lambda functions. It allows any service to record critical failures 
 * to an SQS queue for async processing by amplify-lambda-admin.
 * 
 * The logCriticalError function can be imported and used in any Lambda to record
 * critical errors without requiring admin privileges or special permissions.
 * 
 * Copyright (c) 2025 Vanderbilt University
 * Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
 */

import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';
import { requiredEnvVars, SQSOperation } from './envVarsTracking.js';

// Initialize SQS client
const sqsClient = new SQSClient({});

// Constants
const STATUS_ACTIVE = 'ACTIVE';
const SEVERITY_CRITICAL = 'CRITICAL';
const SEVERITY_HIGH = 'HIGH';
const SEVERITY_MEDIUM = 'MEDIUM';
const SEVERITY_LOW = 'LOW';

/**
 * Internal function with environment variable resolution and tracking.
 * This function assumes CRITICAL_ERRORS_SQS_QUEUE_NAME is available.
 * 
 * @param {Object} params - The error logging parameters
 * @returns {Promise<Object>} Response object
 */
const _logCriticalErrorInternal = requiredEnvVars({
    "CRITICAL_ERRORS_SQS_QUEUE_NAME": [SQSOperation.SEND_MESSAGE]
})(async ({
    functionName,
    errorType,
    errorMessage,
    currentUser = null,
    severity = SEVERITY_CRITICAL,
    stackTrace = null,
    context = null,
    serviceName = null
}) => {
    // Auto-detect service name if not provided
    if (!serviceName) {
        serviceName = process.env.SERVICE_NAME || 'unknown';
    }

    // Get SQS queue URL (guaranteed to be available due to decorator)
    const queueUrl = process.env.CRITICAL_ERRORS_SQS_QUEUE_NAME;

    // Prepare message payload
    const messageBody = {
        service_name: serviceName,
        function_name: functionName,
        error_type: errorType,
        error_message: errorMessage,
        current_user: currentUser,
        severity: severity,
        stack_trace: stackTrace,
        context: context
    };

    // Send to SQS
    const command = new SendMessageCommand({
        QueueUrl: queueUrl,
        MessageBody: JSON.stringify(messageBody),
        MessageAttributes: {
            severity: {
                StringValue: severity,
                DataType: 'String'
            },
            service_name: {
                StringValue: serviceName,
                DataType: 'String'
            }
        }
    });

    await sqsClient.send(command);

    console.log(
        `Critical error queued: ${serviceName}.${functionName} | Type: ${errorType}`
    );

    return {
        success: true,
        message: 'Critical error queued for processing'
    };
});

/**
 * Log a critical error by sending it to an SQS queue for processing.
 * 
 * This function is FAIL-SAFE and will never throw exceptions or block the caller.
 * Errors are sent to SQS asynchronously for processing by amplify-lambda-admin.
 * 
 * This function is intentionally NOT decorated with requiredEnvVars at the top level
 * because it needs to be called from internal service code, not via API endpoints.
 * 
 * @param {Object} params - The error logging parameters
 * @param {string} params.functionName - Specific function/handler name
 *                                       Example: "processWebSocket", "handleMessage"
 * @param {string} params.errorType - Classification of the error
 *                                    Example: "DatabaseConnectionFailure", "S3UploadError"
 * @param {string} params.errorMessage - Detailed error message describing what went wrong
 * @param {string} [params.currentUser=null] - Username/email of the user who triggered the error
 *                                             Example: "user@example.com"
 *                                             If null, will be recorded as "system"
 * @param {string} [params.severity='CRITICAL'] - Error severity level
 *                                                Options: "CRITICAL", "HIGH", "MEDIUM", "LOW"
 * @param {string} [params.stackTrace=null] - Full stack trace for debugging
 *                                            Can be captured via error.stack
 * @param {Object} [params.context=null] - Additional metadata as object
 *                                         Can include: user_id, request_id, 
 *                                         aws_region, custom fields, etc.
 * @param {string} [params.serviceName=null] - Name of the service where error occurred.
 *                                             If null, automatically uses SERVICE_NAME env var.
 *                                             Example: "amplify-lambda-js", "amplify-lambda-api"
 * 
 * @returns {Promise<Object>} Response object with structure:
 *     {
 *         success: true,
 *         message: "Critical error queued for processing"
 *     }
 *     Always returns success=true to indicate fail-safe operation.
 * 
 * @example
 * import { logCriticalError } from './common/criticalLogger.js';
 * 
 * try {
 *     await processPayment(orderId, currentUser);
 * } catch (error) {
 *     await logCriticalError({
 *         functionName: 'processPayment',
 *         errorType: 'PaymentProcessingFailure',
 *         errorMessage: error.message,
 *         currentUser: currentUser,
 *         severity: 'CRITICAL',
 *         stackTrace: error.stack,
 *         context: {
 *             orderId: orderId,
 *             amount: amount
 *         }
 *     });
 * }
 * 
 * Environment Variables Required:
 *     CRITICAL_ERRORS_SQS_QUEUE_NAME: SQS queue URL for critical errors
 *     SERVICE_NAME (optional): Auto-detected service name
 * 
 * Note: This function uses requiredEnvVars decorator internally for Parameter
 * Store fallback and usage tracking, but maintains fail-safe behavior.
 */
async function logCriticalError({
    functionName,
    errorType,
    errorMessage,
    currentUser = null,
    severity = SEVERITY_CRITICAL,
    stackTrace = null,
    context = null,
    serviceName = null
}) {
    // FAIL-SAFE: Try to use the decorated internal function with all benefits
    try {
        return await _logCriticalErrorInternal({
            functionName,
            errorType,
            errorMessage,
            currentUser,
            severity,
            stackTrace,
            context,
            serviceName
        });

    } catch (error) {
        // Check if it's an environment variable error (from envVarsTracking)
        if (error.message && error.message.includes('CRITICAL_ERRORS_SQS_QUEUE_NAME')) {
            // Environment variable not available (no Parameter Store fallback worked)
            console.warn(
                `CRITICAL_ERRORS_SQS_QUEUE_NAME not available, cannot log error: ${serviceName || 'unknown'}.${functionName} - ${errorType}`
            );
            return {
                success: true,
                message: 'Queue URL not configured, error not logged'
            };
        }

        // SQS-specific errors or any other unexpected errors
        const errorCategory = error.name === 'SQSServiceException' ? 'SQS' : 'Unexpected';
        console.error(
            `${errorCategory} error logging critical error (fail-safe mode): ${error.message}`
        );
        return {
            success: true,
            message: `${errorCategory} error, logged locally only`
        };
    }
}

export {
    logCriticalError,
    // Export constants for use in calling code
    STATUS_ACTIVE,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SEVERITY_LOW
};
