//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { getLogger } from "./logging.js";

const logger = getLogger("circuitBreaker");

// 🏠 LOCAL IN-MEMORY CIRCUIT BREAKER STATE
// Resets on Lambda cold starts, but much simpler
const circuitBreakerState = new Map(); // circuitKey -> { errors, requests, openedAt, status }

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
        this.userId = options.userId || null; // 🔑 PER-USER ISOLATION
        this.circuitKey = this.userId ? `${functionName}-user-${this.userId}` : functionName;
        this.maxErrorRate = options.maxErrorRate || 0.25; // 25%
        this.maxCostPerHour = options.maxCostPerHour || 25; // $25/hour
        this.cooldownPeriod = options.cooldownPeriod || 300; // 5 minutes
        this.windowSize = options.windowSize || 60; // 60 seconds
        
        // Initialize local state if it doesn't exist
        if (!circuitBreakerState.has(this.circuitKey)) {
            circuitBreakerState.set(this.circuitKey, {
                errors: 0,
                requests: 0,
                status: "CLOSED",
                lastUpdated: Date.now(),
                openedAt: null
            });
        }
    }

    /**
     * Check if circuit breaker is open (function disabled)
     */
    isOpen() {
        const state = circuitBreakerState.get(this.circuitKey);
        if (!state) return false;

        const now = Date.now();
        
        // Check if still in cooldown period
        if (state.openedAt && (now - state.openedAt) < (this.cooldownPeriod * 1000)) {
            logger.warn(`🚫 PER-USER Circuit breaker OPEN for ${this.circuitKey} - cooldown until ${new Date(state.openedAt + this.cooldownPeriod * 1000)}`);
            return true;
        }

        // Reset if cooldown expired
        if (state.openedAt) {
            this.reset();
        }

        return false;
    }

    /**
     * Record successful request
     */
    recordSuccess() {
        this.updateMetrics(1, 0);
    }

    /**
     * Record failed request
     */
    recordFailure() {
        this.updateMetrics(1, 1);
        
        // Check if we need to open the circuit
        const shouldOpen = this.shouldOpenCircuit();
        if (shouldOpen) {
            this.openCircuit();
        }
    }

    /**
     * Check if circuit should be opened based on error rate
     */
    shouldOpenCircuit() {
        const state = circuitBreakerState.get(this.circuitKey);
        if (!state) return false;

        const now = Date.now();
        const windowStart = now - (this.windowSize * 1000);

        // Only check if we have recent data
        if (!state.lastUpdated || state.lastUpdated < windowStart) return false;

        const errorRate = state.errors / Math.max(state.requests, 1);
        logger.debug(`🎯 PER-USER Error rate for ${this.circuitKey}: ${(errorRate * 100).toFixed(1)}% (${state.errors}/${state.requests})`);

        return errorRate > this.maxErrorRate && state.requests >= 10; // Min 10 requests before tripping
    }

    /**
     * Open the circuit breaker (disable function)
     */
    openCircuit() {
        const now = Date.now();
        const state = circuitBreakerState.get(this.circuitKey);
        
        if (state) {
            state.openedAt = now;
            state.status = "OPEN";
            circuitBreakerState.set(this.circuitKey, state);
        }

        const userInfo = this.userId ? ` for user ${this.userId.substring(0, 10)}...` : '';
        logger.error(`🚨 PER-USER CIRCUIT BREAKER OPENED for ${this.circuitKey}${userInfo} - disabled for ${this.cooldownPeriod}s due to high error rate`);
        
        // Send alert (implement your notification system)
        this.sendAlert(`Per-user circuit breaker opened for ${this.circuitKey} due to high error rate`);
    }

    /**
     * Reset circuit breaker
     */
    reset() {
        circuitBreakerState.set(this.circuitKey, {
            errors: 0,
            requests: 0,
            status: "CLOSED",
            lastUpdated: Date.now(),
            openedAt: null
        });

        logger.info(`🔄 PER-USER Circuit breaker RESET for ${this.circuitKey}`);
    }

    /**
     * Update metrics (requests and errors)
     */
    updateMetrics(requests, errors) {
        const now = Date.now();
        const windowStart = now - (this.windowSize * 1000);
        const state = circuitBreakerState.get(this.circuitKey);
        
        if (!state) {
            // Initialize if doesn't exist
            circuitBreakerState.set(this.circuitKey, {
                errors: errors,
                requests: requests,
                status: "CLOSED",
                lastUpdated: now,
                openedAt: null
            });
            return;
        }
        
        // Reset counters if data is older than window
        if (!state.lastUpdated || state.lastUpdated < windowStart) {
            state.errors = errors;
            state.requests = requests;
        } else {
            state.errors += errors;
            state.requests += requests;
        }
        
        state.lastUpdated = now;
        circuitBreakerState.set(this.circuitKey, state);
    }

    /**
     * Send alert notification
     */
    sendAlert(message) {
        // Implement your alerting system here (SNS, Slack, etc.)
        logger.error(`ALERT: ${message}`);
    }
}

/**
 * 🔑 PER-USER Circuit breaker middleware for Lambda handlers
 * 
 * CRITICAL: Each user gets their own circuit breaker to prevent
 * one problematic user from affecting others
 */
export const withCircuitBreaker = (functionName, options = {}) => {
    return (handler) => {
        return async (event, context, params = null) => {
            // Extract userId from event or params for per-user isolation
            const userId = params?.user || event?.user || event?.requestContext?.authorizer?.user || null;
            
            if (!userId) {
                logger.warn("⚠️ No userId found for circuit breaker - falling back to function-wide protection");
            }
            
            // Create per-user circuit breaker
            const circuitBreaker = new CircuitBreaker(functionName, { ...options, userId });
            
            // Check circuit breaker first
            const isOpen = circuitBreaker.isOpen();
            if (isOpen) {
                const userInfo = userId ? ` for user ${userId.substring(0, 10)}...` : '';
                logger.warn(`🚫 Circuit breaker blocked request${userInfo}`);
                
                return {
                    statusCode: 503,
                    body: JSON.stringify({
                        error: "Service temporarily unavailable - circuit breaker open",
                        retryAfter: circuitBreaker.cooldownPeriod,
                        circuitKey: circuitBreaker.circuitKey
                    }),
                };
            }

            try {
                // Execute the handler
                const result = await handler(event, context, params);
                
                // Record success
                circuitBreaker.recordSuccess();
                
                return result;
            } catch (error) {
                // Record failure
                circuitBreaker.recordFailure();
                
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
        let timeoutId;
        const timeoutPromise = new Promise((_, reject) => {
            timeoutId = setTimeout(() => reject(new Error(`Operation timed out after ${timeoutMs}ms`)), timeoutMs);
        });

        try {
            const result = await Promise.race([asyncOperation, timeoutPromise]);
            clearTimeout(timeoutId); // 🚨 CRITICAL: Clear timer to prevent Lambda hanging
            return result;
        } catch (error) {
            clearTimeout(timeoutId); // 🚨 CRITICAL: Clear timer on error too
            throw error;
        }
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