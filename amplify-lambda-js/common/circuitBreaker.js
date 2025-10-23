//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { DynamoDBClient, GetItemCommand, PutItemCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";
import { getLogger } from "./logging.js";

const logger = getLogger("circuitBreaker");
const dynamodbClient = new DynamoDBClient();

/**
 * 🚨 EMERGENCY COST CIRCUIT BREAKER
 * 
 * Prevents runaway Lambda costs by automatically:
 * 1. Tracking error rates per function
 * 2. Disabling functions when error rate > threshold  
 * 3. Auto-recovery after cool-down period
 * 4. Cost-based shutdowns
 */

export class CircuitBreaker {
    constructor(functionName, options = {}) {
        this.functionName = functionName;
        this.maxErrorRate = options.maxErrorRate || 0.25; // 25%
        this.maxCostPerHour = options.maxCostPerHour || 25; // $25/hour
        this.cooldownPeriod = options.cooldownPeriod || 300; // 5 minutes
        this.windowSize = options.windowSize || 60; // 60 seconds
        this.tableName = process.env.CIRCUIT_BREAKER_TABLE || 'amplify-circuit-breaker';
    }

    /**
     * Check if circuit breaker is open (function disabled)
     */
    async isOpen() {
        try {
            const response = await dynamodbClient.send(new GetItemCommand({
                TableName: this.tableName,
                Key: marshall({ functionName: this.functionName })
            }));

            if (!response.Item) return false;

            const data = unmarshall(response.Item);
            const now = Date.now();
            
            // Check if still in cooldown period
            if (data.openedAt && (now - data.openedAt) < (this.cooldownPeriod * 1000)) {
                logger.warn(`Circuit breaker OPEN for ${this.functionName} - cooldown until ${new Date(data.openedAt + this.cooldownPeriod * 1000)}`);
                return true;
            }

            // Reset if cooldown expired
            if (data.openedAt) {
                await this.reset();
            }

            return false;
        } catch (error) {
            logger.error("Error checking circuit breaker:", error);
            return false; // Fail open to avoid blocking all traffic
        }
    }

    /**
     * Record successful request
     */
    async recordSuccess() {
        await this.updateMetrics(1, 0);
    }

    /**
     * Record failed request
     */
    async recordFailure() {
        await this.updateMetrics(1, 1);
        
        // Check if we need to open the circuit
        const shouldOpen = await this.shouldOpenCircuit();
        if (shouldOpen) {
            await this.openCircuit();
        }
    }

    /**
     * Check if circuit should be opened based on error rate
     */
    async shouldOpenCircuit() {
        try {
            const response = await dynamodbClient.send(new GetItemCommand({
                TableName: this.tableName,
                Key: marshall({ functionName: this.functionName })
            }));

            if (!response.Item) return false;

            const data = unmarshall(response.Item);
            const now = Date.now();
            const windowStart = now - (this.windowSize * 1000);

            // Only check if we have recent data
            if (!data.lastUpdated || data.lastUpdated < windowStart) return false;

            const errorRate = data.errors / Math.max(data.requests, 1);
            logger.debug(`Error rate for ${this.functionName}: ${(errorRate * 100).toFixed(1)}% (${data.errors}/${data.requests})`);

            return errorRate > this.maxErrorRate && data.requests >= 10; // Min 10 requests before tripping
        } catch (error) {
            logger.error("Error calculating error rate:", error);
            return false;
        }
    }

    /**
     * Open the circuit breaker (disable function)
     */
    async openCircuit() {
        try {
            const now = Date.now();
            await dynamodbClient.send(new UpdateItemCommand({
                TableName: this.tableName,
                Key: marshall({ functionName: this.functionName }),
                UpdateExpression: "SET openedAt = :now, #status = :status",
                ExpressionAttributeNames: { "#status": "status" },
                ExpressionAttributeValues: marshall({
                    ":now": now,
                    ":status": "OPEN"
                })
            }));

            logger.error(`🚨 CIRCUIT BREAKER OPENED for ${this.functionName} - function disabled for ${this.cooldownPeriod}s due to high error rate`);
            
            // Send alert (implement your notification system)
            await this.sendAlert(`Circuit breaker opened for ${this.functionName} due to high error rate`);
        } catch (error) {
            logger.error("Error opening circuit breaker:", error);
        }
    }

    /**
     * Reset circuit breaker
     */
    async reset() {
        try {
            await dynamodbClient.send(new PutItemCommand({
                TableName: this.tableName,
                Item: marshall({
                    functionName: this.functionName,
                    requests: 0,
                    errors: 0,
                    status: "CLOSED",
                    lastUpdated: Date.now()
                })
            }));

            logger.info(`Circuit breaker RESET for ${this.functionName}`);
        } catch (error) {
            logger.error("Error resetting circuit breaker:", error);
        }
    }

    /**
     * Update metrics (requests and errors)
     */
    async updateMetrics(requests, errors) {
        try {
            const now = Date.now();
            const windowStart = now - (this.windowSize * 1000);

            await dynamodbClient.send(new UpdateItemCommand({
                TableName: this.tableName,
                Key: marshall({ functionName: this.functionName }),
                UpdateExpression: "ADD requests :req, errors :err SET lastUpdated = :now",
                ExpressionAttributeValues: marshall({
                    ":req": requests,
                    ":err": errors,
                    ":now": now
                }),
                // Reset counters if data is older than window
                ConditionExpression: "attribute_not_exists(lastUpdated) OR lastUpdated >= :windowStart",
                ExpressionAttributeValues: marshall({
                    ":req": requests,
                    ":err": errors,
                    ":now": now,
                    ":windowStart": windowStart
                })
            }));
        } catch (error) {
            if (error.name === 'ConditionalCheckFailedException') {
                // Data is stale, reset and retry
                await this.reset();
                await this.updateMetrics(requests, errors);
            } else {
                logger.error("Error updating metrics:", error);
            }
        }
    }

    /**
     * Send alert notification
     */
    async sendAlert(message) {
        // Implement your alerting system here (SNS, Slack, etc.)
        logger.error(`ALERT: ${message}`);
    }
}

/**
 * Circuit breaker middleware for Lambda handlers
 */
export const withCircuitBreaker = (functionName, options = {}) => {
    return (handler) => {
        const circuitBreaker = new CircuitBreaker(functionName, options);
        
        return async (event, context) => {
            // Check circuit breaker first
            const isOpen = await circuitBreaker.isOpen();
            if (isOpen) {
                return {
                    statusCode: 503,
                    body: JSON.stringify({
                        error: "Service temporarily unavailable - circuit breaker open",
                        retryAfter: circuitBreaker.cooldownPeriod
                    }),
                };
            }

            try {
                // Execute the handler
                const result = await handler(event, context);
                
                // Record success
                await circuitBreaker.recordSuccess();
                
                return result;
            } catch (error) {
                // Record failure
                await circuitBreaker.recordFailure();
                
                throw error;
            }
        };
    };
};

/**
 * Fail-fast request wrapper with timeout
 */
export const withTimeout = (timeoutMs = 30000) => {
    return async (asyncOperation) => {
        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error(`Operation timed out after ${timeoutMs}ms`)), timeoutMs);
        });

        return Promise.race([asyncOperation, timeoutPromise]);
    };
};

/**
 * Exponential backoff retry with jitter
 */
export const withRetry = async (operation, maxRetries = 3, baseDelay = 1000) => {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            return await operation();
        } catch (error) {
            if (attempt === maxRetries) throw error;
            
            // Don't retry on client errors (4xx)
            if (error.response && error.response.status >= 400 && error.response.status < 500) {
                throw error;
            }
            
            // Exponential backoff with jitter
            const delay = baseDelay * Math.pow(2, attempt) + Math.random() * 1000;
            logger.warn(`Retry attempt ${attempt + 1}/${maxRetries + 1} after ${delay}ms:`, error.message);
            
            await new Promise(resolve => setTimeout(resolve, delay));
        }
    }
};