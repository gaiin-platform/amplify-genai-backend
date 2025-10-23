/**
 * Consolidated Cache System - All caching functionality in one place
 * NO SECRETS CACHING - only non-sensitive data
 */

import crypto from 'crypto';
import tiktokenModel from '@dqbd/tiktoken/encoders/cl100k_base.json' with {type: 'json'};
import { Tiktoken } from '@dqbd/tiktoken/lite';
import { getLogger } from './logging.js';

const logger = getLogger("cache");

// ============================================
// SIMPLE IN-MEMORY CACHE IMPLEMENTATION
// ============================================

class UserSpecificCache {
    constructor(name, defaultTTL = 5 * 60 * 1000) {
        this.name = name;
        this.defaultTTL = defaultTTL;
        this.cache = new Map(); // userId -> category -> key -> {value, timestamp}
    }
    
    get(userId, category, key) {
        const userCache = this.cache.get(userId);
        if (!userCache) return null;
        
        const categoryCache = userCache[category];
        if (!categoryCache) return null;
        
        const entry = categoryCache[key];
        if (!entry) return null;
        
        // Check if expired
        if (Date.now() - entry.timestamp > (entry.ttl || this.defaultTTL)) {
            delete categoryCache[key];
            return null;
        }
        
        return entry.value;
    }
    
    set(userId, category, key, value, ttl = null) {
        if (!this.cache.has(userId)) {
            this.cache.set(userId, {});
        }
        
        const userCache = this.cache.get(userId);
        if (!userCache[category]) {
            userCache[category] = {};
        }
        
        userCache[category][key] = {
            value,
            timestamp: Date.now(),
            ttl: ttl || this.defaultTTL
        };
    }
    
    clear(userId) {
        this.cache.delete(userId);
    }
    
    clearAll() {
        this.cache.clear();
    }
}

// ============================================
// CACHE INSTANCES
// ============================================

const userModelCache = new UserSpecificCache('userModels', 5 * 60 * 1000); // 5 minutes
const dataSourceCache = new UserSpecificCache('dataSources', 10 * 60 * 1000); // 10 minutes
const ragResultsCache = new UserSpecificCache('ragResults', 15 * 60 * 1000); // 15 minutes
const globalCache = new UserSpecificCache('global', 30 * 60 * 1000); // 30 minutes for global data

// ============================================
// TOKEN COUNTING WITH CACHE
// ============================================

// Global encoder instance - reused across all requests
let globalEncoder = null;

// Token count cache
const tokenCountCache = new Map();
const TOKEN_CACHE_TTL = 60 * 60 * 1000; // 1 hour

function getGlobalEncoder() {
    if (!globalEncoder) {
        globalEncoder = new Tiktoken(
            tiktokenModel.bpe_ranks,
            tiktokenModel.special_tokens,
            tiktokenModel.pat_str,
        );
        logger.info("Created global Tiktoken encoder instance");
    }
    return globalEncoder;
}

export function countTokensCached(text) {
    if (!text) return 0;
    
    // Create a hash of the text for caching (first 100 chars + length)
    const cacheKey = `${text.substring(0, 100)}:${text.length}`;
    
    // Check cache
    const cached = tokenCountCache.get(cacheKey);
    if (cached && (Date.now() - cached.timestamp) < TOKEN_CACHE_TTL) {
        return cached.count;
    }
    
    try {
        const encoder = getGlobalEncoder();
        const tokens = encoder.encode(text);
        const count = tokens.length;
        
        // Cache the result
        tokenCountCache.set(cacheKey, {
            count,
            timestamp: Date.now()
        });
        
        // Clean up old cache entries periodically
        if (tokenCountCache.size > 10000) {
            cleanupTokenCache();
        }
        
        return count;
    } catch (e) {
        logger.error("Error counting tokens:", e);
        return 0;
    }
}

function cleanupTokenCache() {
    const now = Date.now();
    let removed = 0;
    
    for (const [key, value] of tokenCountCache.entries()) {
        if (now - value.timestamp > TOKEN_CACHE_TTL) {
            tokenCountCache.delete(key);
            removed++;
        }
    }
    
    if (removed > 0) {
        logger.debug(`Cleaned up ${removed} expired token cache entries`);
    }
}

// ============================================
// CACHE MANAGER CLASS
// ============================================

export class CacheManager {
    
    // ============================================
    // USER MODEL CACHING
    // ============================================
    
    static async getCachedUserModels(userId, accessToken) {
        try {
            // Create hash of access token for cache key (don't store raw token)
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            const cached = userModelCache.get(userId, 'models', tokenHash);
            
            if (cached) {
                logger.debug(`Cache HIT: User models for ${userId}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: User models for ${userId}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached user models for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedUserModels(userId, accessToken, modelData) {
        try {
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            userModelCache.set(userId, 'models', tokenHash, modelData);
            logger.debug(`Cached user models for ${userId}`);
        } catch (error) {
            logger.error(`Error caching user models for ${userId}:`, error);
        }
    }
    
    // ============================================
    // DATA SOURCE CACHING
    // ============================================
    
    static async getCachedDataSources(userId, dataSourceIds, options = {}) {
        try {
            const dsKey = `${JSON.stringify(dataSourceIds.sort())}:${JSON.stringify(options)}`;
            const cached = dataSourceCache.get(userId, 'dataSources', dsKey);
            
            if (cached) {
                logger.debug(`Cache HIT: Data sources for ${userId} (${dataSourceIds.length} sources)`);
                return cached;
            }
            
            logger.debug(`Cache MISS: Data sources for ${userId} (${dataSourceIds.length} sources)`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached data sources for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedDataSources(userId, dataSourceIds, resolvedDataSources, options = {}) {
        try {
            const dsKey = `${JSON.stringify(dataSourceIds.sort())}:${JSON.stringify(options)}`;
            dataSourceCache.set(userId, 'dataSources', dsKey, resolvedDataSources);
            logger.debug(`Cached data sources for ${userId} (${dataSourceIds.length} sources)`);
        } catch (error) {
            logger.error(`Error caching data sources for ${userId}:`, error);
        }
    }
    
    // ============================================
    // RAG RESULTS CACHING  
    // ============================================
    
    static async getCachedRAGResults(userId, messagesHash, dataSourcesHash) {
        try {
            const ragKey = `${messagesHash}:${dataSourcesHash}`;
            const cached = ragResultsCache.get(userId, 'rag', ragKey);
            
            if (cached) {
                logger.debug(`Cache HIT: RAG results for ${userId}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: RAG results for ${userId}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached RAG results for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedRAGResults(userId, messagesHash, dataSourcesHash, ragResults) {
        try {
            const ragKey = `${messagesHash}:${dataSourcesHash}`;
            ragResultsCache.set(userId, 'rag', ragKey, ragResults);
            logger.debug(`Cached RAG results for ${userId}`);
        } catch (error) {
            logger.error(`Error caching RAG results for ${userId}:`, error);
        }
    }
    
    // ============================================
    // CONTEXT PROCESSING CACHING
    // ============================================
    
    static async getCachedContexts(userId, dataSource, maxTokens, options) {
        try {
            const contextKey = `${dataSource.id}:${maxTokens}:${JSON.stringify(options)}`;
            const cached = dataSourceCache.get(userId, 'contexts', contextKey);
            
            if (cached) {
                logger.debug(`Cache HIT: Contexts for ${userId} datasource: ${dataSource.id}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: Contexts for ${userId} datasource: ${dataSource.id}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached contexts for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedContexts(userId, dataSource, maxTokens, options, contexts) {
        try {
            const contextKey = `${dataSource.id}:${maxTokens}:${JSON.stringify(options)}`;
            dataSourceCache.set(userId, 'contexts', contextKey, contexts);
            logger.debug(`Cached contexts for ${userId} datasource: ${dataSource.id} (${contexts?.length || 'null'} contexts)`);
        } catch (error) {
            logger.error(`Error caching contexts for ${userId}:`, error);
        }
    }
    
    // ============================================
    // IMAGE CONTENT CACHING
    // ============================================
    
    static async getCachedImageContent(imageKey) {
        try {
            // Use global cache for images (not user-specific)
            const cached = globalCache.get('global', 'imageContent', imageKey);
            
            if (cached) {
                logger.debug(`Cache HIT: Image content for ${imageKey.substring(0, 50)}...`);
                return cached;
            }
            
            logger.debug(`Cache MISS: Image content for ${imageKey.substring(0, 50)}...`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached image content:`, error);
            return null;
        }
    }
    
    static setCachedImageContent(imageKey, content) {
        try {
            // Cache with 30 minute TTL for images
            globalCache.set('global', 'imageContent', imageKey, content, 30 * 60 * 1000);
            const sizeKB = Math.round(content.length / 1024);
            logger.debug(`Cached image content for ${imageKey.substring(0, 50)}... (${sizeKB}KB)`);
        } catch (error) {
            logger.error(`Error caching image content:`, error);
        }
    }
    
    // ============================================
    // USER-DEFINED ASSISTANT CACHING
    // ============================================
    
    static async getCachedUserDefinedAssistant(userId, assistantId, accessToken) {
        try {
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            const cacheKey = `${assistantId}:${tokenHash}`;
            const cached = userModelCache.get(userId, 'userAssistant', cacheKey);
            
            if (cached) {
                logger.debug(`Cache HIT: User-defined assistant ${assistantId} for ${userId}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: User-defined assistant ${assistantId} for ${userId}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached user-defined assistant for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedUserDefinedAssistant(userId, assistantId, accessToken, assistantData) {
        try {
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            const cacheKey = `${assistantId}:${tokenHash}`;
            userModelCache.set(userId, 'userAssistant', cacheKey, assistantData, 600 * 1000); // 10 min TTL
            logger.debug(`Cached user-defined assistant ${assistantId} for ${userId}`);
        } catch (error) {
            logger.error(`Error caching user-defined assistant for ${userId}:`, error);
        }
    }
    
    // ============================================
    // GROUP MEMBERSHIP CACHING
    // ============================================
    
    static async getCachedGroupMembership(userId, groupId, accessToken) {
        try {
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            const membershipKey = `${groupId}:${tokenHash}`;
            const cached = userModelCache.get(userId, 'groupMembership', membershipKey);
            
            if (cached !== null) {
                logger.debug(`Cache HIT: Group membership ${groupId} for ${userId}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: Group membership ${groupId} for ${userId}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached group membership for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedGroupMembership(userId, groupId, accessToken, isMember) {
        try {
            const tokenHash = crypto.createHash('sha256').update(accessToken).digest('hex').slice(0, 16);
            const membershipKey = `${groupId}:${tokenHash}`;
            userModelCache.set(userId, 'groupMembership', membershipKey, isMember, 900 * 1000); // 15 min TTL
            logger.debug(`Cached group membership ${groupId} for ${userId}: ${isMember}`);
        } catch (error) {
            logger.error(`Error caching group membership for ${userId}:`, error);
        }
    }
    
    // ============================================
    // PERMISSION CACHING
    // ============================================
    
    static async getCachedPermission(userId, operation, resource) {
        try {
            const permissionKey = `${operation}:${resource}`;
            const cached = userModelCache.get(userId, 'permission', permissionKey);
            
            if (cached !== null) {
                logger.debug(`Cache HIT: Permission ${operation} on ${resource} for ${userId}`);
                return cached;
            }
            
            logger.debug(`Cache MISS: Permission ${operation} on ${resource} for ${userId}`);
            return null;
        } catch (error) {
            logger.error(`Error getting cached permission for ${userId}:`, error);
            return null;
        }
    }
    
    static setCachedPermission(userId, operation, resource, hasPermission) {
        try {
            const permissionKey = `${operation}:${resource}`;
            userModelCache.set(userId, 'permission', permissionKey, hasPermission, 300 * 1000); // 5 min TTL
            logger.debug(`Cached permission ${operation} on ${resource} for ${userId}: ${hasPermission}`);
        } catch (error) {
            logger.error(`Error caching permission for ${userId}:`, error);
        }
    }
    
    // ============================================
    // CACHE MANAGEMENT
    // ============================================
    
    static clearUserCache(userId) {
        userModelCache.clear(userId);
        dataSourceCache.clear(userId);
        ragResultsCache.clear(userId);
        logger.info(`Cleared all cache entries for user ${userId}`);
    }
    
    static clearAllCaches() {
        userModelCache.clearAll();
        dataSourceCache.clearAll();
        ragResultsCache.clearAll();
        globalCache.clearAll();
        tokenCountCache.clear();
        logger.info("Cleared all cache entries");
    }
}

// Auto-cleanup every 10 minutes
setInterval(cleanupTokenCache, 10 * 60 * 1000);

logger.info("Consolidated cache system initialized");