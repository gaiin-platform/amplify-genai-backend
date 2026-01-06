//Copyright (c) 2025 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas, Sam Hays

/**
 * usageTracking.js - JavaScript equivalent of PyCommon's usage_tracker module
 *
 * Provides Lambda execution metrics tracking for cost calculation and usage monitoring.
 * Designed to be fail-safe - metrics collection failures never impact main Lambda execution.
 */

import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall } from "@aws-sdk/util-dynamodb";
import { getLogger } from "./logging.js";

const logger = getLogger("usageTracking");
const dynamodbClient = new DynamoDBClient();

/**
 * Lambda Execution Metrics - JavaScript equivalent of PyCommon's LambdaExecutionMetrics dataclass
 *
 * Captures all relevant information needed to calculate Lambda execution costs
 * and monitor usage patterns.
 */
export class LambdaExecutionMetrics {
    constructor({
        // Timing
        startTimestamp,
        endTimestamp,
        durationMs,

        // Identity
        user,
        account,
        apiKeyId = null,

        // Request
        operation,
        endpoint,
        apiAccessed,

        // Response
        statusCode,
        success,
        errorType = null,

        // Lambda context
        requestId = null,
        memoryLimitMb = null,
        maxMemoryUsedMb = null,

        // Additional context
        purpose = null,
        eventSource = null,  // For event-driven functions (SQS, DynamoDB Stream, S3, Scheduled)
        serviceName = null,
        functionName = null
    }) {
        this.startTimestamp = startTimestamp;
        this.endTimestamp = endTimestamp;
        this.durationMs = durationMs;
        this.user = user;
        this.account = account;
        this.apiKeyId = apiKeyId;
        this.operation = operation;
        this.endpoint = endpoint;
        this.apiAccessed = apiAccessed;
        this.statusCode = statusCode;
        this.success = success;
        this.errorType = errorType;
        this.requestId = requestId;
        this.memoryLimitMb = memoryLimitMb;
        this.maxMemoryUsedMb = maxMemoryUsedMb;
        this.purpose = purpose;
        this.eventSource = eventSource;
        this.serviceName = serviceName;
        this.functionName = functionName;
    }

    /**
     * Get duration with padding to account for tracking overhead
     *
     * The tracked duration misses:
     * - DynamoDB metrics write time (~50-100ms)
     * - Lambda finalization overhead (~20-50ms)
     * - Response serialization (~10-20ms)
     *
     * @param {number} paddingPercent - Percentage to add to duration (default 25%)
     * @returns {number} Padded duration in milliseconds
     */
    getPaddedDurationMs(paddingPercent = 25.0) {
        return this.durationMs * (1.0 + paddingPercent / 100.0);
    }

    /**
     * Calculate estimated AWS Lambda cost in USD
     *
     * AWS Lambda pricing (as of 2025):
     * - $0.0000166667 per GB-second (compute)
     * - First 1M requests per month are free, then $0.20 per 1M requests
     * - First 400,000 GB-seconds of compute per month are free
     *
     * This calculation focuses on compute cost only (GB-seconds).
     * Request cost is negligible for most use cases.
     *
     * @param {Object} options - Cost calculation options
     * @param {boolean} options.useActualMemory - Use maxMemoryUsedMb instead of memoryLimitMb
     * @param {boolean} options.usePaddedDuration - Add padding to account for tracking overhead
     * @param {number} options.paddingPercent - Percentage to pad duration (default 25%)
     * @returns {number} Estimated cost in USD for this execution
     */
    estimatedCostUsd({
        useActualMemory = false,
        usePaddedDuration = true,
        paddingPercent = 25.0
    } = {}) {
        if (!this.memoryLimitMb || this.durationMs <= 0) {
            return 0.0;
        }

        // Use actual memory if requested and available, otherwise use limit
        // Note: AWS bills based on allocated memory, not used memory
        // But tracking actual usage helps identify over-provisioning
        let memoryMb = this.memoryLimitMb;
        if (useActualMemory && this.maxMemoryUsedMb) {
            memoryMb = this.maxMemoryUsedMb;
        }

        // Convert memory from MB to GB
        const memoryGb = memoryMb / 1024;

        // Use padded duration if requested to account for tracking overhead
        let durationMs = this.durationMs;
        if (usePaddedDuration) {
            durationMs = this.getPaddedDurationMs(paddingPercent);
        }

        // Convert duration from ms to seconds
        const durationSeconds = durationMs / 1000;

        // Calculate GB-seconds
        const gbSeconds = memoryGb * durationSeconds;

        // AWS Lambda cost per GB-second
        const costPerGbSecond = 0.0000166667;

        // Calculate total cost
        const cost = gbSeconds * costPerGbSecond;

        // Round to 10 decimal places
        return parseFloat(cost.toFixed(10));
    }

    /**
     * Convert metrics to DynamoDB item format
     *
     * @returns {Object} Dictionary formatted for DynamoDB storage
     */
    toDynamoDbItem() {
        const item = {
            account: this.account,
            timestamp: this.startTimestamp.toISOString(),
            user: this.user,
            operation: this.operation,
            endpoint: this.endpoint,
            duration_ms: this.durationMs,
            status_code: this.statusCode,
            success: this.success,
            api_accessed: this.apiAccessed,
            estimated_cost_usd: this.estimatedCostUsd(),
        };

        // Add optional fields only if they exist
        if (this.apiKeyId) {
            item.api_key_id = this.apiKeyId;
        }
        if (this.errorType) {
            item.error_type = this.errorType;
        }
        if (this.requestId) {
            item.request_id = this.requestId;
        }
        if (this.memoryLimitMb) {
            item.memory_limit_mb = this.memoryLimitMb;
        }
        if (this.maxMemoryUsedMb) {
            item.max_memory_used_mb = this.maxMemoryUsedMb;
        }
        if (this.purpose) {
            item.purpose = this.purpose;
        }
        if (this.eventSource) {
            item.event_source = this.eventSource;
        }
        if (this.serviceName) {
            item.service_name = this.serviceName;
        }
        if (this.functionName) {
            item.function_name = this.functionName;
        }

        return item;
    }
}

/**
 * Usage Tracker - JavaScript equivalent of PyCommon's UsageTracker class
 *
 * Tracks Lambda execution metrics and stores them in DynamoDB.
 * Provides methods to start tracking at Lambda entry, end tracking at Lambda exit,
 * and store metrics asynchronously.
 *
 * The tracker is designed to be fail-safe - if metrics collection fails,
 * it will log the error but not impact the main Lambda execution.
 */
export class UsageTracker {
    constructor(dynamodbTable = null, enabled = true) {
        this.tableName = dynamodbTable || process.env.ADDITIONAL_CHARGES_TABLE;

        // Check if tracking is enabled via environment variable
        const envEnabled = (process.env.ENABLE_USAGE_TRACKING || 'true').toLowerCase();
        this.enabled = enabled && ['true', '1', 'yes'].includes(envEnabled);

        if (!this.enabled) {
            logger.info("Usage tracking is disabled");
            return;
        }

        if (!this.tableName) {
            logger.warn("ADDITIONAL_CHARGES_TABLE not set, usage tracking will be disabled");
            this.enabled = false;
            return;
        }

        logger.debug(`Usage tracker initialized with table: ${this.tableName}`);
    }

    /**
     * Start tracking a Lambda execution
     *
     * Called at the entry point after authentication and validation.
     *
     * @param {string} user - Username/user ID performing the operation
     * @param {string} operation - Operation type/name
     * @param {string} endpoint - API endpoint/path being called
     * @param {boolean} apiAccessed - Whether this is API access (true) or OAuth (false)
     * @param {Object} context - Lambda context object
     * @returns {Object} Tracking context to pass to endTracking()
     */
    startTracking(user, operation, endpoint, apiAccessed, context, serviceName = null, functionName = null) {
        if (!this.enabled) {
            return {};
        }

        const trackingContext = {
            startTime: new Date(),
            user,
            operation,
            endpoint,
            apiAccessed,
            requestId: context?.awsRequestId || null,
            memoryLimit: context?.memoryLimitInMB || null,
            serviceName: serviceName || process.env.SERVICE_NAME || null,
            functionName: functionName || context?.functionName || null,
        };

        logger.debug(`Started tracking for user=${user}, op=${operation}, endpoint=${endpoint}, service=${trackingContext.serviceName}`);
        return trackingContext;
    }

    /**
     * End tracking and create metrics object
     *
     * Called at the exit point after the Lambda function completes.
     *
     * @param {Object} trackingContext - Context returned from startTracking()
     * @param {Object} result - Result dictionary with statusCode
     * @param {Object} claims - Claims dictionary with account info
     * @param {string} errorType - Type of error if execution failed (null if successful)
     * @returns {LambdaExecutionMetrics|null} Metrics object, or null if tracking disabled
     */
    endTracking(trackingContext, result, claims, errorType = null) {
        if (!this.enabled || !trackingContext || !trackingContext.startTime) {
            return null;
        }

        try {
            const endTime = new Date();
            const startTime = trackingContext.startTime;
            const duration = endTime - startTime; // milliseconds

            const statusCode = result?.statusCode || 200;

            // Capture memory usage (Node.js process RSS + 30MB overhead buffer)
            let maxMemoryUsedMb = null;
            try {
                // Get peak memory usage in bytes, convert to MB
                const memoryUsage = process.memoryUsage();
                const rssBytes = memoryUsage.rss; // Resident Set Size
                const rssMb = Math.floor(rssBytes / (1024 * 1024));

                // Add 30MB buffer for Lambda runtime overhead
                maxMemoryUsedMb = rssMb + 30;
            } catch (e) {
                logger.debug(`Could not capture memory usage: ${e.message}`);
            }

            const metrics = new LambdaExecutionMetrics({
                startTimestamp: startTime,
                endTimestamp: endTime,
                durationMs: duration,
                user: trackingContext.user,
                account: claims?.account || 'unknown',
                apiKeyId: claims?.api_key_id || null,
                operation: trackingContext.operation,
                endpoint: trackingContext.endpoint,
                apiAccessed: trackingContext.apiAccessed,
                statusCode,
                success: statusCode < 400,
                errorType,
                requestId: trackingContext.requestId,
                memoryLimitMb: trackingContext.memoryLimit,
                maxMemoryUsedMb,
                purpose: claims?.purpose || null,
                eventSource: trackingContext.eventSource || null,
                serviceName: trackingContext.serviceName,
                functionName: trackingContext.functionName
            });

            logger.debug(
                `Ended tracking: duration=${duration.toFixed(2)}ms, ` +
                `memory=${maxMemoryUsedMb || 'unknown'}MB, ` +
                `status=${statusCode}, cost=$${metrics.estimatedCostUsd()}`
            );
            return metrics;

        } catch (error) {
            logger.error(`Error ending tracking: ${error.message}`, error);
            return null;
        }
    }

    /**
     * Store metrics in ADDITIONAL_CHARGES_TABLE
     *
     * Format matches code interpreter pattern with all details in 'details' field.
     * This is designed to be fire-and-forget to avoid impacting Lambda response times.
     * Errors are logged but not raised.
     *
     * @param {LambdaExecutionMetrics|null} metrics - Metrics object to store, or null to skip
     * @returns {Promise<void>}
     */
    async recordMetrics(metrics) {
        if (!this.enabled || !metrics) {
            return;
        }

        try {
            const { v4: uuidv4 } = await import('uuid');

            // Generate unique ID for this execution record
            const executionId = `${metrics.user}#lambda#${uuidv4()}`;

            // Calculate cost (top-level for easy querying)
            const cost = metrics.estimatedCostUsd();

            // Calculate TTL: 90 days from now (Lambda records are temporary)
            const ttl = Math.floor(Date.now() / 1000) + (90 * 24 * 60 * 60);

            // Build details object with all execution data
            const details = {
                itemType: "lambda_execution",
                execution: {
                    service_name: metrics.serviceName,
                    function_name: metrics.functionName,
                    operation: metrics.operation,
                    endpoint: metrics.endpoint,
                    event_source: metrics.eventSource,
                    duration_ms: metrics.durationMs,
                    duration_ms_padded: metrics.getPaddedDurationMs(),
                    memory_limit_mb: metrics.memoryLimitMb,
                    max_memory_used_mb: metrics.maxMemoryUsedMb,
                    estimated_cost_usd: cost,  // Based on padded duration by default
                    status_code: metrics.statusCode,
                    success: metrics.success,
                    api_accessed: metrics.apiAccessed,
                    timestamp: metrics.startTimestamp.toISOString(),
                    request_id: metrics.requestId,
                }
            };

            // Add optional fields
            if (metrics.errorType) {
                details.execution.error_type = metrics.errorType;
            }
            if (metrics.apiKeyId) {
                details.execution.api_key_id = metrics.apiKeyId;
            }
            if (metrics.purpose) {
                details.execution.purpose = metrics.purpose;
            }

            // Create ADDITIONAL_CHARGES_TABLE item
            const item = {
                id: executionId,
                user: metrics.user,
                accountId: metrics.account,
                itemType: "lambda_execution",
                cost: cost,  // Top-level cost field for easy aggregation
                ttl: ttl,  // Auto-delete after 90 days
                details: details,
                modelId: `${metrics.serviceName || 'unknown'}/${metrics.functionName || 'unknown'}`,
                time: new Date().toISOString(),
                requestId: metrics.requestId || "unknown",
            };

            const command = new PutItemCommand({
                TableName: this.tableName,
                Item: marshall(item)
            });

            await dynamodbClient.send(command);

            logger.info(
                `Recorded metrics to ADDITIONAL_CHARGES: user=${metrics.user}, ` +
                `service=${metrics.serviceName}, func=${metrics.functionName}, ` +
                `duration=${metrics.durationMs.toFixed(2)}ms, cost=$${metrics.estimatedCostUsd()}`
            );

        } catch (error) {
            // Log error but don't raise - we never want metrics to break the main flow
            logger.error(`Failed to record metrics: ${error.message}`, error);
        }
    }

    /**
     * Detect event source from Lambda event
     *
     * Auto-detects the event source (SQS, DynamoDB Stream, S3, Scheduled, etc.)
     * from the event structure.
     *
     * @param {Object} event - Lambda event object
     * @returns {string|null} Event source type or null if unknown
     */
    detectEventSource(event) {
        if (!event) return null;

        // SQS
        if (event.Records && event.Records[0]?.eventSource === 'aws:sqs') {
            return 'SQS';
        }

        // DynamoDB Stream
        if (event.Records && event.Records[0]?.eventSource === 'aws:dynamodb') {
            return 'DynamoDB_Stream';
        }

        // S3
        if (event.Records && event.Records[0]?.eventSource === 'aws:s3') {
            return 'S3';
        }

        // CloudWatch Scheduled Event
        if (event.source === 'aws.events' && event['detail-type'] === 'Scheduled Event') {
            return 'Scheduled';
        }

        // API Gateway / Lambda Function URL
        if (event.requestContext) {
            return 'API_Gateway';
        }

        return null;
    }
}

// Global singleton instance
let _usageTracker = null;

/**
 * Get or create the global usage tracker instance
 *
 * @returns {UsageTracker} The global usage tracker singleton
 */
export function getUsageTracker() {
    if (_usageTracker === null) {
        _usageTracker = new UsageTracker();
    }
    return _usageTracker;
}

/**
 * Decorator for event-driven Lambda functions (SQS, DynamoDB Streams, S3, Scheduled)
 *
 * JavaScript equivalent of PyCommon's @track_execution decorator.
 * Automatically tracks Lambda execution metrics for cost calculation.
 *
 * Args:
 *     operationName: Name of the operation (e.g., "process_email_event",
 *                    "daily_cleanup_cron", "process_sqs_message")
 *     account: Default account to attribute costs to. Use "system" for
 *              system operations, or specify an account ID.
 *     user: Default user to attribute costs to. Use "system" for system
 *           operations, or specify a user ID.
 *     extractFromEvent: If true, attempts to extract account/user from
 *                       the event payload (default: true)
 *
 * Usage:
 * // SQS handler with system account
 * export const handler = trackExecution("process_sqs_message", "system", "system")(
 *   async (event, context) => {
 *     // Your event handler logic
 *     return { statusCode: 200 };
 *   }
 * );
 *
 * // Scheduled task (cron)
 * export const handler = trackExecution("daily_cleanup_cron")(
 *   async (event, context) => {
 *     // Your cleanup logic
 *     return { success: true };
 *   }
 * );
 *
 * @param {string} operationName - Name of the operation being performed
 * @param {string} account - Default account (default: "system")
 * @param {string} user - Default user (default: "system")
 * @param {boolean} extractFromEvent - Extract user/account from event (default: true)
 * @returns {Function} Decorator function that takes the handler
 */
export function trackExecution(operationName, account = 'system', user = 'system', extractFromEvent = true) {
    // Validate environment variable
    if (!process.env.ADDITIONAL_CHARGES_TABLE) {
        logger.warn("ADDITIONAL_CHARGES_TABLE not set, tracking will be disabled");
    }

    return function decorator(handlerFunction) {
        return async (event, context) => {
            const tracker = getUsageTracker();

            if (!tracker.enabled) {
                // Tracking disabled, just execute the function
                return await handlerFunction(event, context);
            }

            // Detect event source
            const eventSource = tracker.detectEventSource(event);

            // Extract user/account info from event (best effort)
            let actualUser = user;
            let actualAccount = account;

            if (extractFromEvent) {
                // Try to extract from top-level event
                actualAccount = event.account || event.accountId || actualAccount;
                actualUser = event.user || event.currentUser || event.userId || actualUser;

                // Try to extract from SQS message body
                if (eventSource === 'SQS' && event.Records && event.Records[0]) {
                    try {
                        const body = JSON.parse(event.Records[0].body);
                        actualUser = body.user || body.username || actualUser;
                        actualAccount = body.account || body.accountId || actualAccount;

                        // Check if body contains nested S3 event (like embedding SQS->S3 pattern)
                        if (body.Records && body.Records[0] && body.Records[0].s3) {
                            const s3Key = decodeURIComponent(body.Records[0].s3.object.key.replace(/\+/g, ' '));
                            const keyParts = s3Key.split('/');
                            if (keyParts.length > 0 && actualUser === user) {
                                // First part of key is usually the user
                                const potentialUser = keyParts[0];
                                // Validate it looks like a user (email or UUID format)
                                if (potentialUser.includes('@') || (potentialUser.includes('-') && potentialUser.length > 20)) {
                                    actualUser = potentialUser;
                                }
                            }
                        }
                    } catch (e) {
                        // Ignore parse errors
                    }
                }

                // Try to extract from DynamoDB Stream
                if (eventSource === 'DynamoDB_Stream' && event.Records && event.Records[0]) {
                    const newImage = event.Records[0].dynamodb?.NewImage;
                    if (newImage) {
                        actualUser = newImage.user?.S || newImage.user_id?.S || newImage.username?.S || actualUser;
                        actualAccount = newImage.account?.S || newImage.accountId?.S || actualAccount;
                    }
                }

                // Try to extract from direct S3 event (not wrapped in SQS)
                if (eventSource === 'S3' && event.Records && event.Records[0] && event.Records[0].s3) {
                    try {
                        const s3Key = decodeURIComponent(event.Records[0].s3.object.key.replace(/\+/g, ' '));
                        const keyParts = s3Key.split('/');
                        if (keyParts.length > 0) {
                            const potentialUser = keyParts[0];
                            if (potentialUser.includes('@') || (potentialUser.includes('-') && potentialUser.length > 20)) {
                                actualUser = potentialUser;
                            }
                        }
                    } catch (e) {
                        // Ignore errors
                    }
                }
            }

            // Create endpoint identifier for tracking (matches Python format)
            const endpoint = `event://${eventSource || 'unknown'}/${operationName}`;

            // Start tracking
            const trackingContext = tracker.startTracking(
                actualUser,
                operationName,
                endpoint,
                false, // Event-driven = not API accessed
                context
            );
            trackingContext.eventSource = eventSource;

            let result;
            let errorType = null;

            try {
                // Execute the original handler
                result = await handlerFunction(event, context);

                // Determine success from result
                let success = true;
                if (result && typeof result === 'object') {
                    success = result.success !== undefined ? result.success : true;
                    if (!success && result.error) {
                        errorType = result.error;
                    }
                }

                return result;
            } catch (error) {
                errorType = error.constructor.name || 'Error';
                throw error;
            } finally {
                // End tracking and record metrics
                try {
                    const metrics = tracker.endTracking(
                        trackingContext,
                        result || { statusCode: errorType ? 500 : 200 },
                        { account: actualAccount, user: actualUser },
                        errorType
                    );

                    // Fire and forget - don't await
                    if (metrics) {
                        tracker.recordMetrics(metrics).catch(err =>
                            logger.error(`Background metrics recording failed: ${err.message}`)
                        );
                    }
                } catch (metricsError) {
                    // Never let metrics break the function
                    logger.error(`Metrics tracking failed: ${metricsError.message}`);
                }
            }
        };
    };
}
