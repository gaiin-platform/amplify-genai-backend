import {DynamoDBClient, QueryCommand, ScanCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";
import axios from "axios";

const logger = getLogger("rateLimiter");

// Cache for lifetime cost calculations within a single request
let lifetimeCostCache = new Map();


async function calculateTotalLifetimeCost(userEmail, accountInfo) {
    const cacheKey = `${userEmail}:${accountInfo || 'all'}`;
    
    // Return cached result if available
    if (lifetimeCostCache.has(cacheKey)) {
        logger.debug(`Using cached lifetime cost for ${userEmail}`);
        return lifetimeCostCache.get(cacheKey);
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
        
        // 1. Scan HISTORY table for all historical monthly costs (need ALL history, not just current month)
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
            // Direct query for specific accountInfo - much more efficient
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
            // If no specific accountInfo, query all user records (fallback)
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
        
        // Cache the result for this request
        lifetimeCostCache.set(cacheKey, totalLifetimeCost);
        return totalLifetimeCost;
        
    } catch (error) {
        logger.error('Error calculating total lifetime cost:', error);
        return 0; // Default to 0 on error to avoid blocking
    }
}


/**
 * Calculate if a specific rate limit is exceeded
 */
async function calcIsRateLimited(limit, rateData, params) {
    //periods include Monthly, Daily, Hourly, Total
    const period = limit.period
    let spent = 0;
    
    if (period === 'Total') {
        // Calculate total lifetime cost
        spent = await calculateTotalLifetimeCost(params.user, rateData.accountInfo);
    } else {
        const colName = `${period.toLowerCase()}Cost`
        spent = rateData[colName];
        if (period === 'Hourly') spent = spent[new Date().getHours()]// Get the current hour as a number from 0 to 23
    }
    
    const isRateLimited = spent >= limit.rate;
    if (isRateLimited) {
        params.body.options.rateLimit.currentSpent = spent;
    }
    return isRateLimited;
}

/**
 * Helper function to check a single rate limit and set violation info
 */
async function checkAndSetLimit(limit, rateData, params, limitType, isAdminSet = false, groupName = null) {
    const isLimited = await calcIsRateLimited(limit, rateData, params);
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
 * Get user's affiliated groups and their rate limits
 */
async function getUserGroupRateLimits(accessToken) {
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
        
        return groupRateLimits;
        
    } catch (error) {
        logger.error('Error getting user group rate limits:', error);
        return []; // Return empty array on error to avoid blocking
    }
}


export async function isRateLimited(params) {
    // Clear cache for fresh request to avoid stale data
    lifetimeCostCache.clear();
    
    const userRateLimit = params.body.options.rateLimit;
    const adminRateLimit = await getAdminRateLimit();
    // Skip group rate limits for API key authentication (no OAuth token)
    const groupRateLimits = params.accessToken ? 
        await getUserGroupRateLimits(params.accessToken) : [];
    
    const noLimit = (limit) => {
        return !limit || limit.period?.toLowerCase() === 'unlimited';
    }
    
    // Simple rate limit checking order:
    // 1. Admin limit - if violated, reject immediately
    // 2. Group limits - check each group, if any violated, reject
    // 3. User limit - only if admin + groups all pass

    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

    if (!costCalcTable) {
        console.log("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
    }

    try {
        const dynamodbClient = new DynamoDBClient();
        const command = new QueryCommand({
            TableName: costCalcTable,
            KeyConditionExpression: '#id = :userid',
            ExpressionAttributeNames: {
                '#id': 'id'  // Using an expression attribute name to avoid any potential keyword conflicts
            },
            ExpressionAttributeValues: {
                ':userid': { S: params.user} // Assuming this is the id you are querying by
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

        // Function now defined at module level
        // 1. Check admin limit first
        if (!noLimit(adminRateLimit)) {
            if (await checkAndSetLimit(adminRateLimit, rateData, params, 'admin', true)) {
                return true;
            }
        }
        
        // 2. Check each group limit
        for (const groupLimit of groupRateLimits) {
            if (!noLimit(groupLimit)) {
                if (await checkAndSetLimit(groupLimit, rateData, params, 'group', false, groupLimit.groupName)) {
                    return true;
                }
            }
        }
        
        // 3. Finally check user limit
        if (!noLimit(userRateLimit)) {
            if (await checkAndSetLimit(userRateLimit, rateData, params, 'user')) {
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


async function getAdminRateLimit() {
    const adminTable = process.env.ADMIN_DYNAMODB_TABLE;

    if (!adminTable) {
        console.log("ADMIN_DYNAMODB_TABLE is not provided in the environment variables.");
        throw new Error("ADMIN_DYNAMODB_TABLE is not provided in the environment variables.");
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
            logger.error("Table entry does not exist. Can not verify if rate limited");
            return false;
        }
        const rateData = unmarshall(item);
        console.log(rateData)
        return rateData.data;
        
    } catch (error) {
        console.error("Error during rate limit DynamoDB operation:", error);
        // let it slide for now
        return false;
    }


}

export const formatRateLimit = (limit) =>  {
    if (!limit || limit.rate === undefined || limit.rate === null) return "No limit";
    return `$${limit.rate.toFixed(2)} / ${limit.period}`;
}

export const formatCurrentSpent = (limit) =>  {
    if (limit.currentSpent === undefined || limit.currentSpent === null) return "";
    const periodDisplay = {
        "Daily": "today",
        "Hourly": "this hour",
        "Monthly": "this month",
        "Total": "in total"
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
    }
    
    return `$${limit.currentSpent} spent ${periodText}${limitSource}.`;
}