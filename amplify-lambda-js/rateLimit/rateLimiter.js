import {DynamoDBClient, QueryCommand, ScanCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import axios from "axios";

const logger = getLogger("rateLimiter");

// 💰 SMART CACHING: Multiple cache layers for performance
let adminRateLimitCache = null; // Admin limits (rarely change)
let adminRateLimitCacheTime = 0;
let groupRateLimitsCache = new Map(); // Group limits by user
let lifetimeCalculationCache = new Map(); // Expensive lifetime calculations

// 🛡️ PROGRESSIVE RATE LIMITING: Track consecutive violations
let rateLimitViolations = new Map(); // userId -> { count, lastViolation, timeoutUntil }
const CONSECUTIVE_LIMIT_THRESHOLD = 5; // 5 consecutive hits = ban
const VIOLATION_WINDOW_MS = 60 * 1000; // 1 minute window (keeping the charming "wiolation widow"!)
const SHORT_TIMEOUT_MS = 60 * 1000; // 1 minute timeout
const LONG_TIMEOUT_MS = 15 * 60 * 1000; // 15 minute timeout


async function calculateTotalLifetimeCost(userEmail, accountInfo) {
    const cacheKey = `${userEmail}:${accountInfo || 'all'}`;
    
    // 💰 Check 30-second cache for expensive lifetime calculations
    const lifetimeCached = lifetimeCalculationCache.get(cacheKey);
    if (lifetimeCached && (Date.now() - lifetimeCached.timestamp) < 30 * 1000) {
        logger.debug(`Using 30s cached lifetime cost for ${userEmail}`);
        return lifetimeCached.value;
    }
    
    const dynamodbClient = new DynamoDBClient();
    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
    const historyTable = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;
    
    if (!costCalcTable || !historyTable) {
        logger.error('Missing required table environment variables for lifetime cost calculation');
        return 0;
    }
    
    try {
        let totalLifetimeCost = 0;
        
        // 1. Scan HISTORY table for all historical monthly costs
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
                if (cost > 0) totalLifetimeCost += cost;
            }
        }
        
        // 2. Query current COST_CALCULATIONS table for current month
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
                const currentMonthTotal = dailyCost + monthlyCost;
                totalLifetimeCost += currentMonthTotal;
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
                    const currentMonthTotal = dailyCost + monthlyCost;
                    totalLifetimeCost += currentMonthTotal;
                }
            }
        }
        
        logger.debug(`Total lifetime cost for ${userEmail}: ${totalLifetimeCost}`);
        
        // 💰 CACHE: Store for 30 seconds
        lifetimeCalculationCache.set(cacheKey, {
            value: totalLifetimeCost,
            timestamp: Date.now()
        });
        
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
    } else {
        const colName = `${period.toLowerCase()}Cost`;
        spent = rateData[colName];
        if (period === 'Hourly') spent = spent[new Date().getHours()];
    }
    
    const isRateLimited = spent >= limit.rate;
    if (isRateLimited) {
        params.body.options.rateLimit.currentSpent = spent;
    }
    return isRateLimited;
}

/**
 * Helper function to check a single rate limit and set violation info
 * Now accepts precomputed lifetime cost
 */
async function checkAndSetLimit(limit, rateData, params, limitType, isAdminSet = false, groupName = null, precomputedLifetimeCost = null) {
    const isLimited = await calcIsRateLimited(limit, rateData, params, precomputedLifetimeCost);
    if (isLimited) {
        params.body.options.rateLimit = {
            ...limit,
            limitType,
            adminSet: isAdminSet,
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
 * 🧹 CLEANUP: Remove old violation records to prevent memory leaks
 */
function cleanupOldViolations() {
    const now = Date.now();
    const maxAge = 24 * 60 * 60 * 1000; // 24 hours
    
    for (const [userId, violations] of rateLimitViolations.entries()) {
        if (now - violations.lastViolation > maxAge) {
            rateLimitViolations.delete(userId);
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
    
    // Get rate limits from various sources
    const userRateLimit = params.body.options.rateLimit; // NOT cached - from request
    const adminRateLimit = await getAdminRateLimit(); // Cached 10 min
    const groupRateLimits = params.accessToken ? 
        await getUserGroupRateLimits(params.accessToken) : []; // Cached 5 min
    
    const noLimit = (limit) => {
        return !limit || limit.period?.toLowerCase() === 'unlimited';
    }
    
    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

    if (!costCalcTable) {
        console.log("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
    }

    try {
        // 📊 OPTIMIZATION: Fetch user's cost data ONCE
        const dynamodbClient = new DynamoDBClient();
        const command = new QueryCommand({
            TableName: costCalcTable,
            KeyConditionExpression: '#id = :userid',
            ExpressionAttributeNames: {
                '#id': 'id'
            },
            ExpressionAttributeValues: {
                ':userid': { S: params.user}
            }
        });
        
        logger.debug("Calling billing table.");
        const response = await dynamodbClient.send(command);
        
        const item = response.Items[0];

        if (!item) {
            logger.error("Table entry does not exist. Can not verify if rate limited");
            return false;
        }
        const rateData = unmarshall(item);

        // 🚀 OPTIMIZATION: Pre-calculate lifetime cost ONCE if any limit needs it
        let precomputedLifetimeCost = null;
        const needsLifetimeCost = 
            (!noLimit(adminRateLimit) && adminRateLimit.period === 'Total') ||
            groupRateLimits.some(g => !noLimit(g) && g.period === 'Total') ||
            (!noLimit(userRateLimit) && userRateLimit.period === 'Total');
        
        if (needsLifetimeCost) {
            logger.debug("Pre-calculating lifetime cost once for all rate limit checks");
            precomputedLifetimeCost = await calculateTotalLifetimeCost(params.user, rateData.accountInfo);
        }

        // Check limits in order: admin -> groups -> user
        
        // 1. Check admin limit first
        if (!noLimit(adminRateLimit)) {
            if (await checkAndSetLimit(adminRateLimit, rateData, params, 'admin', true, null, precomputedLifetimeCost)) {
                recordRateLimitViolation(params.user);
                return true;
            }
        }
        
        // 2. Check each group limit
        for (const groupLimit of groupRateLimits) {
            if (!noLimit(groupLimit)) {
                if (await checkAndSetLimit(groupLimit, rateData, params, 'group', false, groupLimit.groupName, precomputedLifetimeCost)) {
                    recordRateLimitViolation(params.user);
                    return true;
                }
            }
        }
        
        // 3. Finally check user limit
        if (!noLimit(userRateLimit)) {
            if (await checkAndSetLimit(userRateLimit, rateData, params, 'user', false, null, precomputedLifetimeCost)) {
                recordRateLimitViolation(params.user);
                return true;
            }
        }
        
        return false;
        
    } catch (error) {
        console.error("Error during rate limit DynamoDB operation:", error);
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
        console.log("AMPLIFY_ADMIN_DYNAMODB_TABLE is not provided in the environment variables.");
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
            console.log(`❌ No admin rate limit config found in table: ${adminTable}`);
            logger.error("Table entry does not exist. Can not verify if rate limited");
            return false;
        }
        const rateData = unmarshall(item);
        
        // 💰 CACHE: Store admin rate limit for 10 minutes
        adminRateLimitCache = rateData.data;
        adminRateLimitCacheTime = Date.now();
        
        return rateData.data;
        
    } catch (error) {
        console.error("Error during rate limit DynamoDB operation:", error);
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
    }
    
    return `$${limit.currentSpent} spent ${periodText}${limitSource}.`;
}