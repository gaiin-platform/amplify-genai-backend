import {DynamoDBClient, QueryCommand, ScanCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import axios from "axios";

const logger = getLogger("rateLimiter");

// 💰 SMART CACHING: Multiple cache layers for performance
let adminRateLimitCache = null; // Admin limits (rarely change)
let adminRateLimitCacheTime = 0;
let groupRateLimitsCache = new Map(); // Group limits by user
let historyCostCache = new Map(); // Cache ONLY historical costs (never changes)
let lastPassedLimitCache = new Map();

// 🛡️ PROGRESSIVE RATE LIMITING: Track consecutive violations
let rateLimitViolations = new Map(); // userId -> { count, lastViolation, timeoutUntil }
const CONSECUTIVE_LIMIT_THRESHOLD = 10; // 10 consecutive hits = ban
const VIOLATION_WINDOW_MS = 60 * 1000; // 1 minute window (keeping the charming "wiolation widow"!)
const SHORT_TIMEOUT_MS = 60 * 1000; // 1 minute timeout
const LONG_TIMEOUT_MS = 15 * 60 * 1000; // 15 minute timeout

// 🚨 ERROR-BASED RATE LIMITING: Track consecutive errors
let errorViolations = new Map(); // userId -> { count, lastError, timeoutUntil }
const ERROR_LIMIT_THRESHOLD = 20; // 20 consecutive errors = ban
const ERROR_WINDOW_MS = 5 * 60 * 1000; // 5 minute window
const ERROR_TIMEOUT_MS = 10 * 60 * 1000; // 10 minute timeout for errors


async function calculateTotalLifetimeCost(userEmail, accountInfo) {
    const now = new Date();
    const currentMonth = now.getMonth();
    const currentYear = now.getFullYear();
    const cacheKey = `${userEmail}:${accountInfo || 'all'}:${currentMonth}-${currentYear}`;

    const dynamodbClient = new DynamoDBClient();
    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
    const historyTable = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

    if (!costCalcTable || !historyTable) {
        logger.error('Missing required table environment variables for lifetime cost calculation');
        return 0;
    }

    try {
        let historicalCost = 0;

        // 1. Check cache for HISTORY (never changes, safe to cache)
        const cachedHistory = historyCostCache.get(cacheKey);
        if (cachedHistory) {
            logger.debug(`Using cached historical cost for ${userEmail}`);
            historicalCost = cachedHistory;
        } else {
            // Scan HISTORY table for all historical monthly costs
            const historyCommand = new ScanCommand({
                TableName: historyTable,
                FilterExpression: accountInfo ?
                    'begins_with(userDate, :userEmail) AND accountInfo = :accountInfo AND attribute_exists(monthlyCost) AND monthlyCost > :zero' :
                    'begins_with(userDate, :userEmail) AND attribute_exists(monthlyCost) AND monthlyCost > :zero',
                ExpressionAttributeValues: accountInfo ? {
                    ':userEmail': { S: `${userEmail}#` },
                    ':accountInfo': { S: accountInfo },
                    ':zero': { N: '0' }
                } : {
                    ':userEmail': { S: `${userEmail}#` },
                    ':zero': { N: '0' }
                },
                ProjectionExpression: 'monthlyCost'
            });

            const historyResponse = await dynamodbClient.send(historyCommand);

            if (historyResponse.Items) {
                for (const item of historyResponse.Items) {
                    const historyData = unmarshall(item);
                    const cost = parseFloat(historyData.monthlyCost) || 0;
                    if (cost > 0) historicalCost += cost;
                }
            }

            // Cache historical cost (never changes)
            historyCostCache.set(cacheKey, historicalCost);
            logger.debug(`Cached historical cost for ${userEmail}: ${historicalCost}`);
        }

        // 2. ALWAYS fetch current month FRESH (changes every request)
        let currentMonthCost = 0;

        if (accountInfo) {
            // Direct query for specific accountInfo
            const currentCommand = new QueryCommand({
                TableName: costCalcTable,
                KeyConditionExpression: '#id = :userid AND accountInfo = :accountInfo',
                ExpressionAttributeNames: {
                    '#id': 'id'
                },
                ExpressionAttributeValues: {
                    ':userid': { S: userEmail },
                    ':accountInfo': { S: accountInfo }
                },
                ProjectionExpression: 'monthlyCost, dailyCost'
            });

            const currentResponse = await dynamodbClient.send(currentCommand);

            if (currentResponse.Items && currentResponse.Items.length > 0) {
                const currentData = unmarshall(currentResponse.Items[0]);
                const dailyCost = parseFloat(currentData.dailyCost) || 0;
                const monthlyCost = parseFloat(currentData.monthlyCost) || 0;
                currentMonthCost = dailyCost + monthlyCost;
            }
        } else {
            // If no specific accountInfo, query all user records
            const currentCommand = new QueryCommand({
                TableName: costCalcTable,
                KeyConditionExpression: '#id = :userid',
                ExpressionAttributeNames: {
                    '#id': 'id'
                },
                ExpressionAttributeValues: {
                    ':userid': { S: userEmail }
                },
                ProjectionExpression: 'monthlyCost, dailyCost'
            });

            const currentResponse = await dynamodbClient.send(currentCommand);

            if (currentResponse.Items) {
                for (const item of currentResponse.Items) {
                    const currentData = unmarshall(item);
                    const dailyCost = parseFloat(currentData.dailyCost) || 0;
                    const monthlyCost = parseFloat(currentData.monthlyCost) || 0;
                    currentMonthCost += dailyCost + monthlyCost;
                }
            }
        }

        const totalLifetimeCost = historicalCost + currentMonthCost;
        logger.debug(`Total lifetime cost for ${userEmail}: ${totalLifetimeCost} (history: ${historicalCost}, current: ${currentMonthCost})`);

        return totalLifetimeCost;

    } catch (error) {
        logger.error('Error calculating total lifetime cost:', error);
        return 0; // Default to 0 on error to avoid blocking
    }
}


/**
 * Calculate if a specific rate limit is exceeded
 * Now accepts precomputed lifetime cost to avoid recalculation
 */
async function calcIsRateLimited(limit, rateData, params, precomputedLifetimeCost = null) {
    const period = limit.period;
    let spent = 0;

    if (period === 'Total') {
        // Use precomputed cost if available, otherwise calculate
        spent = precomputedLifetimeCost !== null ?
            precomputedLifetimeCost :
            await calculateTotalLifetimeCost(params.user, rateData.accountInfo);
    } else if (period === 'Monthly') {
        const dailyCost = parseFloat(rateData.dailyCost) || 0;
        const monthlyCost = parseFloat(rateData.monthlyCost) || 0;
        spent = dailyCost + monthlyCost;
    } else if (period === 'Hourly') {
        const hourlyCost = rateData.hourlyCost || [];
        const currentHour = new Date().getHours();
        spent = parseFloat(hourlyCost[currentHour]?.N || hourlyCost[currentHour] || 0);
    } else if (period === 'Daily') {
        spent = parseFloat(rateData.dailyCost) || 0;
    } else {
        const colName = `${period.toLowerCase()}Cost`;
        spent = rateData[colName];
    }

    const isRateLimited = spent >= limit.rate;
    if (isRateLimited) {
        params.body.options.rateLimit.currentSpent = spent;
    }
    return { isRateLimited, spent };
}

/**
 * Helper function to check a single rate limit and set violation info
 * Now accepts precomputed lifetime cost
 */
async function checkAndSetLimit(limit, rateData, params, limitType, isAdminSet = false, groupName = null, precomputedLifetimeCost = null) {
    const { isRateLimited, spent } = await calcIsRateLimited(limit, rateData, params, precomputedLifetimeCost);
    if (isRateLimited) {
        params.body.options.rateLimit = {
            ...limit,
            limitType,
            adminSet: isAdminSet,
            currentSpent: spent,
            ...(groupName && { groupName })
        };
        return true;
    }
    return false;
}

/**
 * 💰 CACHED: Get user's affiliated groups and their rate limits
 * Caches for 5 minutes since group memberships rarely change
 */
async function getUserGroupRateLimits(accessToken) {
    const cacheKey = `groups:${accessToken.slice(-8)}`; // Use token suffix for caching
    const cached = groupRateLimitsCache.get(cacheKey);

    if (cached && (Date.now() - cached.timestamp) < 5 * 60 * 1000) {
        logger.debug('Using cached group rate limits');
        return cached.data;
    }
    try {
        const apiBaseUrl = process.env.API_BASE_URL;
        if (!apiBaseUrl) {
            logger.warn('API_BASE_URL not configured, skipping group rate limits');
            return [];
        }
        
        // Call the admin service to get user's affiliated groups
        const response = await axios.get(`${apiBaseUrl}/amplifymin/amplify_groups/affiliated`, {
            headers: {
                'Authorization': `Bearer ${accessToken}`
            }
        });
        
        if (!response.data || !response.data.success) {
            logger.warn('Failed to get user affiliated groups');
            return [];
        }
        
        const groupRateLimits = [];
        const allGroups = response.data.all_groups || {};
        const affiliatedGroups = response.data.data || [];
        
        // Extract rate limits from all affiliated groups
        for (const groupName of affiliatedGroups) {
            const groupData = allGroups[groupName];
            if (groupData && groupData.rateLimit) {
                groupRateLimits.push({
                    ...groupData.rateLimit,
                    groupName,
                    isGroupLimit: true
                });
            }
        }
        
        // 💰 CACHE: Store group rate limits for 5 minutes
        groupRateLimitsCache.set(cacheKey, {
            data: groupRateLimits,
            timestamp: Date.now()
        });
        
        return groupRateLimits;
        
    } catch (error) {
        logger.error('Error getting user group rate limits:', error);
        // Return cached data if available, even if stale
        if (cached) {
            logger.debug('Using stale cached group rate limits due to error');
            return cached.data;
        }
        return []; // Return empty array on error to avoid blocking
    }
}


/**
 * 🛡️ PROGRESSIVE PUNISHMENT: Check if user is in timeout
 */
function isUserInTimeout(userId) {
    const violations = rateLimitViolations.get(userId);
    if (!violations) return false;
    
    const now = Date.now();
    if (violations.timeoutUntil && now < violations.timeoutUntil) {
        const remainingMs = violations.timeoutUntil - now;
        logger.debug(`User ${userId} is in timeout for ${Math.ceil(remainingMs / 1000)}s more`);
        return { inTimeout: true, remainingMs };
    }
    
    return false;
}

/**
 * 🛡️ PROGRESSIVE PUNISHMENT: Record rate limit violation
 */
function recordRateLimitViolation(userId) {
    const now = Date.now();
    let violations = rateLimitViolations.get(userId) || { count: 0, lastViolation: 0, timeoutUntil: 0 };
    
    // Reset count if it's been more than violation window since last violation
    if (now - violations.lastViolation > VIOLATION_WINDOW_MS) {
        violations.count = 0;
    }
    
    violations.count++;
    violations.lastViolation = now;
    
    // Apply progressive punishment
    if (violations.count >= CONSECUTIVE_LIMIT_THRESHOLD) {
        // Check if this is a repeated pattern
        const isRepeatedOffender = violations.timeoutUntil > 0;
        
        if (isRepeatedOffender) {
            // Escalate to 15-minute timeout
            violations.timeoutUntil = now + LONG_TIMEOUT_MS;
            logger.warn(`🛡️ ESCALATED BAN: User ${userId} banned for 15min (repeated offender, ${violations.count} consecutive violations)`);
        } else {
            // First offense: 1-minute timeout
            violations.timeoutUntil = now + SHORT_TIMEOUT_MS;
            logger.warn(`🛡️ PROGRESSIVE BAN: User ${userId} banned for 1min (${violations.count} consecutive violations)`);
        }
        
        // Reset violation count after applying timeout
        violations.count = 0;
    } else {
        logger.debug(`🛡️ Rate limit violation ${violations.count}/${CONSECUTIVE_LIMIT_THRESHOLD} for user ${userId}`);
    }
    
    rateLimitViolations.set(userId, violations);
    
    return violations;
}

/**
 * 🚨 ERROR-BASED RATE LIMITING: Check if user is in error timeout
 */
function isUserInErrorTimeout(userId) {
    const violations = errorViolations.get(userId);
    if (!violations) return false;
    
    const now = Date.now();
    if (violations.timeoutUntil && now < violations.timeoutUntil) {
        const remainingMs = violations.timeoutUntil - now;
        logger.debug(`User ${userId} is in error timeout for ${Math.ceil(remainingMs / 1000)}s more`);
        return { inTimeout: true, remainingMs, reason: 'consecutive_errors' };
    }
    
    return false;
}

/**
 * 🚨 ERROR-BASED RATE LIMITING: Record consecutive error
 */
function recordErrorViolation(userId) {
    const now = Date.now();
    let violations = errorViolations.get(userId) || { count: 0, lastError: 0, timeoutUntil: 0 };
    
    // Reset count if it's been more than error window since last error
    if (now - violations.lastError > ERROR_WINDOW_MS) {
        violations.count = 0;
    }
    
    violations.count++;
    violations.lastError = now;
    
    // Apply error-based punishment
    if (violations.count >= ERROR_LIMIT_THRESHOLD) {
        violations.timeoutUntil = now + ERROR_TIMEOUT_MS;
        logger.warn(`🚨 ERROR BAN: User ${userId} banned for 10min (${violations.count} consecutive errors in ${ERROR_WINDOW_MS/60000}min)`);
        
        // Reset error count after applying timeout
        violations.count = 0;
    } else {
        logger.debug(`🚨 Error violation ${violations.count}/${ERROR_LIMIT_THRESHOLD} for user ${userId}`);
    }
    
    errorViolations.set(userId, violations);
    
    return violations;
}

/**
 * 🧹 CLEANUP: Remove old violation records to prevent memory leaks
 */
function cleanupOldViolations() {
    const now = Date.now();
    const maxAge = 24 * 60 * 60 * 1000; // 24 hours
    
    // Clean rate limit violations
    for (const [userId, violations] of rateLimitViolations.entries()) {
        if (now - violations.lastViolation > maxAge) {
            rateLimitViolations.delete(userId);
        }
    }
    
    // Clean error violations
    for (const [userId, violations] of errorViolations.entries()) {
        if (now - violations.lastError > maxAge) {
            errorViolations.delete(userId);
        }
    }
}

export async function isRateLimited(params) {
    // 🧹 CLEANUP: Periodically clean old records
    if (Math.random() < 0.01) cleanupOldViolations(); // 1% chance
    
    
    // 🛡️ EARLY EXIT: Check if user is in progressive timeout
    const timeoutStatus = isUserInTimeout(params.user);
    if (timeoutStatus.inTimeout) {
        params.body.options.rateLimit = {
            period: 'Progressive',
            rate: 0,
            currentSpent: 0,
            limitType: 'progressive_timeout',
            adminSet: true,
            timeoutRemaining: Math.ceil(timeoutStatus.remainingMs / 1000)
        };
        return true; // User is banned
    }
    
    // 🚨 EARLY EXIT: Check if user is in error timeout
    const errorTimeoutStatus = isUserInErrorTimeout(params.user);
    if (errorTimeoutStatus.inTimeout) {
        params.body.options.rateLimit = {
            period: 'Error',
            rate: 0,
            currentSpent: 0,
            limitType: 'error_timeout',
            adminSet: true,
            timeoutRemaining: Math.ceil(errorTimeoutStatus.remainingMs / 1000),
            reason: errorTimeoutStatus.reason
        };
        return true; // User is banned
    }
    
    const userRateLimit = params.body.options.rateLimit;

    const noLimit = (limit) => {
        return !limit || limit.period?.toLowerCase() === 'unlimited';
    }

    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

    if (!costCalcTable) {
        logger.error("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
    }

    try {
        const accountId = params.body?.options?.accountId || 'general_account';
        const apiKeyId = params.apiKeyId || 'NA';
        const accountInfo = `${accountId}#${apiKeyId}`;
        const dynamodbClient = new DynamoDBClient();
        const command = new QueryCommand({
            TableName: costCalcTable,
            KeyConditionExpression: '#id = :userid AND accountInfo = :accountInfo',
            ExpressionAttributeNames: {
                '#id': 'id'
            },
            ExpressionAttributeValues: {
                ':userid': { S: params.user },
                ':accountInfo': { S: accountInfo }
            }
        });

        logger.debug("Calling billing table with specific accountInfo.");
        const response = await dynamodbClient.send(command);

        let rateData;
        let precomputedLifetimeCost = null;

        if (!response.Items || response.Items.length === 0) {
            logger.warn(`No cost record found for accountInfo="${accountInfo}". User may not have any usage yet.`);
            rateData = {
                monthlyCost: 0,
                dailyCost: 0,
                hourlyCost: new Array(24).fill(0),
                accountInfo: accountInfo
            };

        } else {
            const item = response.Items[0];
            rateData = unmarshall(item);
        }

        if (!noLimit(userRateLimit)) {
            if (userRateLimit.period === 'Total') {
                logger.debug("Pre-calculating lifetime cost for user personal limit check");
                precomputedLifetimeCost = await calculateTotalLifetimeCost(params.user, rateData.accountInfo);
                logger.debug(`Total lifetime cost for ${params.user}: ${precomputedLifetimeCost}`);
            }

            const userLimited = await checkAndSetLimit(userRateLimit, rateData, params, 'user', false, null, precomputedLifetimeCost);

            if (userLimited) {
                recordRateLimitViolation(params.user);
                return true;
            }
        }
        const adminRateLimit = await getAdminRateLimit();
        const groupRateLimits = params.accessToken ?
            await getUserGroupRateLimits(params.accessToken) : [];

        if (precomputedLifetimeCost === null) {
            const needsLifetimeCost =
                (!noLimit(adminRateLimit) && adminRateLimit.period === 'Total') ||
                groupRateLimits.some(g => !noLimit(g) && g.period === 'Total');

            if (needsLifetimeCost) {
                logger.debug("Pre-calculating lifetime cost for pool checks");
                precomputedLifetimeCost = await calculateTotalLifetimeCost(params.user, rateData.accountInfo);
            }
        }
        const limitPool = [];

        // Add all group limits to the pool
        for (const groupLimit of groupRateLimits) {
            if (!noLimit(groupLimit)) {
                limitPool.push({
                    limit: groupLimit,
                    limitType: 'group',
                    groupName: groupLimit.groupName
                });
            }
        }

        // Add admin limit to the pool
        if (!noLimit(adminRateLimit)) {
            limitPool.push({
                limit: adminRateLimit,
                limitType: 'admin',
                isAdminSet: true
            });
        }

        const lastPassed = lastPassedLimitCache.get(params.user);
        if (lastPassed) {
            const lastPassedIndex = limitPool.findIndex(entry =>
                entry.limitType === lastPassed.limitType &&
                entry.limit.period === lastPassed.period &&
                (entry.limitType !== 'group' || entry.groupName === lastPassed.groupName)
            );

            if (lastPassedIndex !== -1) {
                const [fastTrack] = limitPool.splice(lastPassedIndex, 1);
                limitPool.unshift(fastTrack);
            }
        }
        const periodPriority = { 'Hourly': 1, 'Daily': 2, 'Monthly': 3, 'Total': 4 };
        const firstItem = lastPassed && limitPool.length > 0 ? limitPool.shift() : null;
        limitPool.sort((a, b) =>
            (periodPriority[a.limit.period] || 3) - (periodPriority[b.limit.period] || 3)
        );
        if (firstItem) limitPool.unshift(firstItem);
        if (limitPool.length > 0) {
            let passedAny = false;
            let failedLimits = [];

            for (const entry of limitPool) {
                const { isRateLimited, spent } = await calcIsRateLimited(entry.limit, rateData, params, precomputedLifetimeCost);

                if (!isRateLimited) {
                    passedAny = true;
                    lastPassedLimitCache.set(params.user, {
                        limitType: entry.limitType,
                        period: entry.limit.period,
                        groupName: entry.groupName
                    });
                    break;
                } else {
                    failedLimits.push({
                        ...entry,
                        currentSpent: spent
                    });
                }
            }

            if (!passedAny && failedLimits.length > 0) {
                const mostGenerous = failedLimits.reduce((max, curr) =>
                    curr.limit.rate > max.limit.rate ? curr : max
                );

                params.body.options.rateLimit = {
                    ...mostGenerous.limit,
                    limitType: mostGenerous.limitType,
                    adminSet: mostGenerous.isAdminSet || false,
                    currentSpent: mostGenerous.currentSpent,
                    ...(mostGenerous.groupName && { groupName: mostGenerous.groupName })
                };
                recordRateLimitViolation(params.user);
                return true;
            }
        }

        return false;
        
    } catch (error) {
        logger.error("Error during rate limit DynamoDB operation:", error);
        // let it slide for now
        return false;
    }

}


/**
 * 💰 CACHED: Get admin rate limit configuration 
 * Caches for 10 minutes since admin settings rarely change
 */
async function getAdminRateLimit() {
    // Check cache first
    const cacheAge = Date.now() - adminRateLimitCacheTime;
    if (adminRateLimitCache && cacheAge < 10 * 60 * 1000) {
        logger.debug('Using cached admin rate limit');
        return adminRateLimitCache;
    }
    
    const adminTable = process.env.AMPLIFY_ADMIN_DYNAMODB_TABLE;

    if (!adminTable) {
        logger.error("AMPLIFY_ADMIN_DYNAMODB_TABLE is not provided in the environment variables.");
        throw new Error("AMPLIFY_ADMIN_DYNAMODB_TABLE is not provided in the environment variables.");
    }
    
     try {
        const dynamodbClient = new DynamoDBClient();
        const command = new QueryCommand({
            TableName: adminTable,
            KeyConditionExpression: "config_id = :rateLimit",
            ExpressionAttributeValues: {
                ":rateLimit": { S: "rateLimit" }, 
            },
        });
        
        logger.debug("Calling admin table for rate limit.");
        const response = await dynamodbClient.send(command);
        
        const item = response.Items[0];

        if (!item) {
            logger.warn(`❌ No admin rate limit config found in table: ${adminTable}`);
            logger.error("Table entry does not exist. Can not verify if rate limited");
            return false;
        }
        const rateData = unmarshall(item);
        
        // 💰 CACHE: Store admin rate limit for 10 minutes
        adminRateLimitCache = rateData.data;
        adminRateLimitCacheTime = Date.now();
        
        return rateData.data;
        
    } catch (error) {
        logger.error("Error during rate limit DynamoDB operation:", error);
        // Return cached data if available, even if stale
        if (adminRateLimitCache) {
            logger.debug('Using stale cached admin rate limit due to error');
            return adminRateLimitCache;
        }
        return false;
    }
}

export const formatRateLimit = (limit) =>  {
    if (!limit || limit.rate === undefined || limit.rate === null) return "No limit";
    return `$${limit.rate.toFixed(2)} / ${limit.period}`;
}

export const formatCurrentSpent = (limit) =>  {
    // 🛡️ PROGRESSIVE PUNISHMENT: Handle timeout messages
    if (limit.limitType === 'progressive_timeout') {
        const minutes = Math.ceil(limit.timeoutRemaining / 60);
        return `You have been temporarily banned for ${minutes > 1 ? `${minutes} minutes` : `${limit.timeoutRemaining} seconds`} due to repeated rate limit violations. Please try again later.`;
    }
    
    // 🚨 ERROR-BASED PUNISHMENT: Handle error timeout messages  
    if (limit.limitType === 'error_timeout') {
        const minutes = Math.ceil(limit.timeoutRemaining / 60);
        return `You have been temporarily banned for ${minutes > 1 ? `${minutes} minutes` : `${limit.timeoutRemaining} seconds`} due to repeated system errors. Please try again later.`;
    }
    
    if (limit.currentSpent === undefined || limit.currentSpent === null) return "";
    const periodDisplay = {
        "Daily": "today",
        "Hourly": "this hour",
        "Monthly": "this month",
        "Total": "in total",
        "Progressive": "due to violations"
    };
    const periodText = periodDisplay[limit.period] || limit.period.toLowerCase();
    
    // Add context about which type of limit was hit
    let limitSource = "";
    if (limit.limitType === 'admin') {
        limitSource = " (Admin limit)";
    } else if (limit.limitType === 'group' && limit.groupName) {
        limitSource = ` (Group: ${limit.groupName})`;
    } else if (limit.limitType === 'user') {
        limitSource = " (User limit)";
    } else if (limit.limitType === 'progressive_timeout') {
        limitSource = " (Progressive timeout)";
    } else if (limit.limitType === 'error_timeout') {
        limitSource = " (Error timeout)";
    }
    
    return `$${limit.currentSpent} spent ${periodText}${limitSource}.`;
}

// Export error violation recording for use in router catch blocks
export { recordErrorViolation };