//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { getLogger } from "./logging.js";
import { withTimeout, withRetry } from "./circuitBreaker.js";

const logger = getLogger("defensiveRouting");

// 🚫 NEGATIVE CACHING: Cache invalid models for instant rejection
const globalInvalidModels = new Map(); // modelId -> { timestamp, error } - for config errors
const userInvalidModels = new Map(); // userId -> Map(modelId -> { timestamp, error }) - for access errors
const INVALID_CACHE_TTL = 300000; // 5 minutes
const cacheStats = { 
    globalHits: 0, 
    userHits: 0, 
    misses: 0 
}; // Performance tracking

/**
 * 🛡️ DEFENSIVE MODEL VALIDATION
 * 
 * Prevents the specific TypeError that caused cost spikes like:
 * "Cannot read properties of undefined (reading 'us.anthropic.claude-3-5-haiku-20241022-v1:0')"
 * 
 * This happened when model configurations were deployed without proper validation.
 */

/**
 * Clean expired entries from both global and user-specific invalid model caches
 */
const cleanInvalidCache = () => {
    const now = Date.now();
    
    // Clean global cache
    for (const [modelId, cached] of globalInvalidModels.entries()) {
        if (now - cached.timestamp > INVALID_CACHE_TTL) {
            globalInvalidModels.delete(modelId);
            logger.debug(`Removed expired global invalid model: ${modelId}`);
        }
    }
    
    // Clean user-specific caches
    for (const [userId, userCache] of userInvalidModels.entries()) {
        for (const [modelId, cached] of userCache.entries()) {
            if (now - cached.timestamp > INVALID_CACHE_TTL) {
                userCache.delete(modelId);
                logger.debug(`Removed expired user-specific invalid model: ${userId}/${modelId}`);
            }
        }
        // Remove empty user caches
        if (userCache.size === 0) {
            userInvalidModels.delete(userId);
        }
    }
};

/**
 * Get user's invalid model cache, creating if needed
 */
const getUserCache = (userId) => {
    if (!userInvalidModels.has(userId)) {
        userInvalidModels.set(userId, new Map());
    }
    return userInvalidModels.get(userId);
};

/**
 * Validate model configuration exists and has required properties
 * @param {Object} models - Available models configuration
 * @param {string} modelId - Model ID to validate
 * @param {Object} modelRequest - Original model request (optional)
 * @param {string} userId - User ID for per-user caching (optional)
 */
export const validateModelConfiguration = (models, modelId, modelRequest = null, userId = null) => {
    const startTime = Date.now();
    
    try {
        // 🚫 GLOBAL CACHE CHECK: Instant rejection for config-level invalid models
        const globalCachedInvalid = globalInvalidModels.get(modelId);
        if (globalCachedInvalid && (Date.now() - globalCachedInvalid.timestamp) < INVALID_CACHE_TTL) {
            cacheStats.globalHits++;
            logger.warn(`⚡ GLOBAL INSTANT REJECTION: Model ${modelId} (${Date.now() - startTime}ms) - Global cache hit #${cacheStats.globalHits}`);
            throw globalCachedInvalid.error;
        }
        
        // 🚫 USER CACHE CHECK: Instant rejection for user-specific invalid models
        if (userId) {
            const userCache = getUserCache(userId);
            const userCachedInvalid = userCache.get(modelId);
            if (userCachedInvalid && (Date.now() - userCachedInvalid.timestamp) < INVALID_CACHE_TTL) {
                cacheStats.userHits++;
                logger.warn(`⚡ USER INSTANT REJECTION: User ${userId} denied model ${modelId} (${Date.now() - startTime}ms) - User cache hit #${cacheStats.userHits}`);
                throw userCachedInvalid.error;
            }
        }
        
        cacheStats.misses++;
        
        // 1. Validate modelId exists
        if (!modelId) {
            const error = new Error("Model ID is required but not provided");
            error.code = "MISSING_MODEL_ID";
            error.statusCode = 400;
            throw error;
        }

        // 2. Validate models object exists
        if (!models || typeof models !== 'object') {
            const error = new Error("Models configuration is missing or invalid");
            error.code = "INVALID_MODELS_CONFIG";
            error.statusCode = 500;
            logger.error("Models configuration missing:", { modelId, modelsType: typeof models });
            throw error;
        }

        // 3. Check if model exists in user's available models (USER-SPECIFIC ERROR)
        const model = models[modelId];
        if (!model) {
            const error = new Error(`Model '${modelId}' not available for this user`);
            error.code = "MODEL_NOT_AVAILABLE";
            error.statusCode = 403; // Forbidden - user doesn't have access
            error.isUserSpecific = true; // Flag for caching strategy
            
            // Log available models for debugging (truncated)
            const availableModels = Object.keys(models).slice(0, 10);
            logger.error("Model not available for user:", { 
                requestedModel: modelId, 
                availableModels,
                totalModels: Object.keys(models).length,
                userId: userId ? userId.substring(0, 10) + '...' : 'unknown',
                requestSource: modelRequest ? "user_request" : "system"
            });
            throw error;
        }

        // 4. Validate model has required properties
        const requiredProperties = ['id', 'provider'];
        const missingProperties = requiredProperties.filter(prop => !model[prop]);
        
        if (missingProperties.length > 0) {
            const error = new Error(`Model '${modelId}' missing required properties: ${missingProperties.join(', ')}`);
            error.code = "INVALID_MODEL_CONFIG";
            error.statusCode = 500;
            error.isUserSpecific = false; // Global configuration issue
            logger.error("Model configuration invalid:", { 
                modelId, 
                missingProperties,
                modelKeys: Object.keys(model)
            });
            throw error;
        }

        // 5. Additional safety checks (GLOBAL ERRORS)
        if (typeof model.id !== 'string' || model.id.length === 0) {
            const error = new Error(`Model '${modelId}' has invalid ID property`);
            error.code = "INVALID_MODEL_ID";
            error.statusCode = 500;
            error.isUserSpecific = false; // Global configuration issue
            throw error;
        }

        logger.debug(`Model validation passed for ${modelId} in ${Date.now() - startTime}ms`);
        
        // 🧹 Periodic cleanup and stats logging (every 100 successful validations)
        if (Math.random() < 0.01) {
            cleanInvalidCache();
            const stats = getCacheStats();
            logger.info(`Invalid model cache stats: ${JSON.stringify(stats)}`);
        }
        
        return model;

    } catch (error) {
        // Ensure error has proper structure for circuit breaker
        if (!error.statusCode) {
            error.statusCode = 500;
        }
        if (!error.code) {
            error.code = "MODEL_VALIDATION_ERROR";
        }
        if (error.isUserSpecific === undefined) {
            error.isUserSpecific = false; // Default to global caching
        }
        
        // 🚫 SMART NEGATIVE CACHING: Cache based on error type
        if (error.isUserSpecific && userId) {
            // USER-SPECIFIC ERROR: Cache per user (e.g., user doesn't have access to GPT-4)
            const userCache = getUserCache(userId);
            userCache.set(modelId, {
                timestamp: Date.now(),
                error: error
            });
            
            logger.error("Model validation failed - cached per user:", {
                modelId,
                userId: userId.substring(0, 10) + '...',
                error: error.message,
                code: error.code,
                duration: Date.now() - startTime,
                userCacheSize: userCache.size,
                totalUserCaches: userInvalidModels.size
            });
        } else {
            // GLOBAL ERROR: Cache for all users (e.g., model doesn't exist in config)
            globalInvalidModels.set(modelId, {
                timestamp: Date.now(),
                error: error
            });
            
            logger.error("Model validation failed - cached globally:", {
                modelId,
                error: error.message,
                code: error.code,
                duration: Date.now() - startTime,
                globalCacheSize: globalInvalidModels.size
            });
        }
        
        throw error;
    }
};

/**
 * 🧠 DYNAMIC LEARNING SYSTEM
 * 
 * No hardcoding, no env vars - the system learns bad models naturally:
 * 1. User requests invalid model -> gets cached (global or per-user)
 * 2. Subsequent requests -> instant rejection from cache
 * 3. Cache expires after 5 minutes in case model becomes valid
 * 
 * This prevents both $741 config incidents AND creates efficient per-user learning!
 */

/**
 * Get cache statistics for monitoring
 */
export const getCacheStats = () => {
    const totalHits = cacheStats.globalHits + cacheStats.userHits;
    const totalRequests = totalHits + cacheStats.misses;
    
    return {
        ...cacheStats,
        globalCacheSize: globalInvalidModels.size,
        userCacheCount: userInvalidModels.size,
        totalUserCacheEntries: Array.from(userInvalidModels.values()).reduce((sum, cache) => sum + cache.size, 0),
        totalHits,
        hitRate: totalRequests > 0 ? ((totalHits / totalRequests) * 100).toFixed(1) + '%' : '0%',
        globalHitRate: totalRequests > 0 ? ((cacheStats.globalHits / totalRequests) * 100).toFixed(1) + '%' : '0%',
        userHitRate: totalRequests > 0 ? ((cacheStats.userHits / totalRequests) * 100).toFixed(1) + '%' : '0%'
    };
};

/**
 * Defensive model selection with fallbacks
 */
export const selectModelWithFallback = (models, preferredModelId, userModelData = null, userId = null) => {
    const fallbackChain = [
        preferredModelId,
        userModelData?.cheapest?.id,
        userModelData?.advanced?.id,
        'gpt-3.5-turbo', // Common fallback
        'claude-3-haiku-20240307', // Another fallback
        Object.keys(models)[0] // Last resort: first available model
    ].filter(Boolean);

    for (const modelId of fallbackChain) {
        try {
            return validateModelConfiguration(models, modelId, null, userId);
        } catch (error) {
            logger.warn(`Fallback failed for model ${modelId}:`, error.message);
            continue;
        }
    }

    // No fallback worked
    const error = new Error("No valid model available in configuration");
    error.code = "NO_VALID_MODELS";
    error.statusCode = 500;
    throw error;
};

/**
 * Validate request body structure to prevent downstream errors
 */
export const validateRequestBody = (body) => {
    const errors = [];

    // Required fields
    if (!body) {
        errors.push("Request body is required");
    } else {
        if (!body.messages && !body.killSwitch && !body.datasourceRequest) {
            errors.push("Request must contain messages, killSwitch, or datasourceRequest");
        }

        if (body.messages && !Array.isArray(body.messages)) {
            errors.push("Messages must be an array");
        }

        if (body.messages && body.messages.length === 0) {
            errors.push("Messages array cannot be empty");
        }

        // Validate options structure if present
        if (body.options && typeof body.options !== 'object') {
            errors.push("Options must be an object");
        }

        // Check for dangerous Unicode content that caused chat_convert failures
        if (body.messages) {
            for (let i = 0; i < body.messages.length; i++) {
                const message = body.messages[i];
                if (message.content && typeof message.content === 'string') {
                    // Check for Unicode surrogate pairs that cause encoding errors
                    if (/[\uD800-\uDBFF][\uDC00-\uDFFF]/.test(message.content)) {
                        logger.warn(`Unicode surrogate pairs detected in message ${i}, sanitizing...`);
                        // Replace surrogate pairs with safe characters
                        body.messages[i].content = message.content.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, '?');
                    }
                    
                    // Check for other problematic characters
                    if (message.content.length > 100000) { // 100k character limit
                        errors.push(`Message ${i} exceeds maximum length (100,000 characters)`);
                    }
                }
            }
        }
    }

    if (errors.length > 0) {
        const error = new Error(`Request validation failed: ${errors.join('; ')}`);
        error.code = "INVALID_REQUEST";
        error.statusCode = 400;
        error.validationErrors = errors;
        throw error;
    }

    return true;
};

/**
 * Defensive external API call wrapper
 */
export const safeExternalCall = async (operation, operationName, timeoutMs = 30000) => {
    const startTime = Date.now();
    
    try {
        logger.debug(`Starting ${operationName}...`);
        
        // Wrap with timeout to prevent long hangs
        const result = await withTimeout(timeoutMs)(
            withRetry(operation, 2, 1000) // Max 2 retries with 1s base delay
        );
        
        logger.debug(`${operationName} completed in ${Date.now() - startTime}ms`);
        return result;
        
    } catch (error) {
        const duration = Date.now() - startTime;
        logger.error(`${operationName} failed after ${duration}ms:`, error.message);
        
        // Enhance error for better debugging
        error.operationName = operationName;
        error.duration = duration;
        
        throw error;
    }
};

/**
 * Cost-aware request processing wrapper
 */
export const withCostMonitoring = (handler) => {
    return async (...args) => {
        const startTime = Date.now();
        const memoryMB = parseInt(process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE) || 1024;
        
        try {
            const result = await handler(...args);
            
            const durationMs = Date.now() - startTime;
            const gbSeconds = (memoryMB / 1024) * (durationMs / 1000);
            const estimatedCost = gbSeconds * 0.0000166667; // AWS Lambda pricing
            
            if (estimatedCost > 0.01) { // Log if cost > $0.01
                logger.warn("High cost request detected:", {
                    durationMs,
                    gbSeconds,
                    estimatedCost: `$${estimatedCost.toFixed(4)}`,
                    memoryMB
                });
            }
            
            return result;
            
        } catch (error) {
            const durationMs = Date.now() - startTime;
            const gbSeconds = (memoryMB / 1024) * (durationMs / 1000);
            const wastedCost = gbSeconds * 0.0000166667;
            
            logger.error("Request failed - wasted cost:", {
                error: error.message,
                durationMs,
                wastedCost: `$${wastedCost.toFixed(4)}`,
                memoryMB
            });
            
            throw error;
        }
    };
};

/**
 * Memory leak prevention for EventEmitter listeners
 */
export const safeEventListener = (emitter, event, listener, options = {}) => {
    const { once = false } = options;
    // 🚫 REMOVED: setTimeout cleanup to prevent Lambda hanging
    
    if (once) {
        emitter.once(event, listener);
    } else {
        emitter.on(event, listener);
        // 🚫 NO AUTO-CLEANUP TIMERS: Let Lambda garbage collection handle it
    }
    
    // Return cleanup function for manual cleanup if needed
    return () => emitter.removeListener(event, listener);
};