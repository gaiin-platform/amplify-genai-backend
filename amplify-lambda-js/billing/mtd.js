import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, ScanCommand, BatchGetCommand } from "@aws-sdk/lib-dynamodb";
import { extractParams } from "../common/handlers.js";
import { getLogger } from "../common/logging.js";

const logger = getLogger("mtd");
const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyCostDynamoTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;
const apiKeysTableName = process.env.API_KEYS_DYNAMODB_TABLE;

const mtdHandler = async (event, context, callback) => {
    try {
        logger.debug("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            return params; // This is an error response from extractParams
        }
        
        const { body, user } = params;
        if (!user) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Email is required' }),
            };
        }

        const email = user;

        const queryParams = {
            TableName: costDynamoTableName,
            KeyConditionExpression: 'id = :email',
            ExpressionAttributeValues: {
                ':email': email,
            },
        };

        const command = new QueryCommand(queryParams);
        const result = await dynamoDB.send(command);

        if (result.Items.length === 0) {
            return {
                statusCode: 200,
                body: JSON.stringify({
                    email: email,
                    dailyCost: 0,
                    monthlyCost: 0,
                    'MTD Cost': 0,
                }),
            };
        }

        let totalDailyCost = 0;
        let totalMonthlyCost = 0;

        result.Items.forEach(item => {
            totalDailyCost += parseFloat(item.dailyCost) || 0;
            totalMonthlyCost += parseFloat(item.monthlyCost) || 0;
        });

        const totalCost = totalDailyCost + totalMonthlyCost;

        return {
            statusCode: 200,
            body: JSON.stringify({
                email: email,
                dailyCost: totalDailyCost,
                monthlyCost: totalMonthlyCost,
                'MTD Cost': totalCost,
            }),
        };
    } catch (error) {
        logger.error("Error processing request: " + error.message, error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

// Optimized helper function to get both purpose and ID for any API key in single query
const getApiKeyDetails = async (identifier) => {
    if (!apiKeysTableName) {
        return { purpose: null, id: identifier };
    }

    try {
        let command;
        
        if (identifier.startsWith('amp-')) {
            // It's an API key - query by apiKey field using GSI
            command = new QueryCommand({
                TableName: apiKeysTableName,
                IndexName: 'ApiKeyIndex',
                KeyConditionExpression: 'apiKey = :apiKeyVal',
                ExpressionAttributeValues: {
                    ':apiKeyVal': identifier
                },
                ProjectionExpression: 'api_owner_id, purpose'
            });
        } else {
            // It's an api_owner_id - query by primary key
            command = new QueryCommand({
                TableName: apiKeysTableName,
                KeyConditionExpression: 'api_owner_id = :ownerIdVal',
                ExpressionAttributeValues: {
                    ':ownerIdVal': identifier
                },
                ProjectionExpression: 'api_owner_id, purpose'
            });
        }

        const response = await dynamoDB.send(command);
        const item = response.Items?.[0];
        
        if (item) {
            return {
                purpose: item.purpose || null,
                id: item.api_owner_id || identifier
            };
        } else {
            return {
                purpose: null,
                id: `legacy_${identifier}`
            };
        }
        
    } catch (error) {
        logger.warn(`Failed to get API key details: ${identifier}`, error);
        return { purpose: null, id: `legacy_${identifier}` };
    }
};

// Helper function to extract real user from systemKey api_owner_id
const extractRealUserFromSystemKey = (apiOwnerId) => {
    if (apiOwnerId && apiOwnerId.includes('/systemKey/')) {
        // Extract everything before "/systemKey/" as the real user
        return apiOwnerId.split('/systemKey/')[0];
    }
    return null;
};

// Helper function to get systemKey costs for a specific user
const getSystemKeyCostsForUser = async (userEmail) => {
    if (!costDynamoTableName) {
        return [];
    }

    try {
        // Query the GSI for cost records
        const queryParams = {
            TableName: costDynamoTableName,
            IndexName: 'record-type-user-index',
            KeyConditionExpression: 'record_type = :type',
            FilterExpression: 'contains(accountInfo, :userSystemKey)',
            ExpressionAttributeValues: {
                ':type': 'cost',
                ':userSystemKey': `${userEmail}/systemKey/`
            },
            ProjectionExpression: 'accountInfo, dailyCost, monthlyCost, #time',
            ExpressionAttributeNames: {
                '#time': 'time'
            }
        };

        const result = await dynamoDB.send(new QueryCommand(queryParams));
        
        if (result.Items && result.Items.length > 0) {
            logger.debug(`Found ${result.Items.length} systemKey cost records for user ${userEmail}`);
            return result.Items;
        }
        
        return [];
        
    } catch (error) {
        logger.warn(`Failed to get systemKey costs for user ${userEmail}:`, error);
        return [];
    }
};

// Helper function to process accountInfo with purpose-based naming
const processAccountInfo = async (accountInfo) => {
    if (!accountInfo || accountInfo === 'Unknown Account') {
        return accountInfo;
    }

    const [account, access] = accountInfo.split('#');
    if (!access || access === 'NA') {
        return accountInfo; // Keep as-is for NA or missing access
    }

    const { purpose, id } = await getApiKeyDetails(access);
    const newAccount = purpose ? `${purpose}_account` : account;
    return `${newAccount}#${id}`;
};

const internalApiKeyUserCostHandler = async (event, context, callback) => {
    try {
        const params = await extractParams(event);
        if (params.statusCode) return params;

        const { body, user } = params;
        if (!body?.data?.apiKeys || !Array.isArray(body.data.apiKeys) || !user) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'API keys array and email are required' }),
            };
        }

        const apiKeys = body.data.apiKeys;
        const email = user;

        if (!historyCostDynamoTableName || !costDynamoTableName) {
            logger.error("Missing required table names");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        // Resolve all amp- keys to their IDs in parallel
        const resolvedKeys = await Promise.all(
            apiKeys.map(async (key) => ({
                original: key,
                resolved: await resolveApiKeyToId(key)
            }))
        );

        // Build key mapping for efficient lookup
        const keyMapping = new Map();
        resolvedKeys.forEach(({ original, resolved }) => {
            keyMapping.set(original, resolved);
            keyMapping.set(resolved, original);
        });

        // Query current costs efficiently using GSI if available
        const currentCostPromises = apiKeys.map(async (apiKey) => {
            const resolvedKey = keyMapping.get(apiKey) || apiKey;
            
            // Query by user email to get all their cost records
            const queryParams = {
                TableName: costDynamoTableName,
                KeyConditionExpression: 'id = :email',
                ExpressionAttributeValues: {
                    ':email': email,
                },
                ProjectionExpression: 'accountInfo, dailyCost, monthlyCost, hourlyCost, record_type'
            };

            const result = await dynamoDB.send(new QueryCommand(queryParams));
            
            // Filter for records matching this API key
            const matchingRecords = result.Items?.filter(item => {
                if (!item.accountInfo) return false;
                const [, access] = item.accountInfo.split('#');
                return access === apiKey || access === resolvedKey;
            }) || [];

            return { apiKey, records: matchingRecords };
        });

        // Process history table more efficiently using parallel queries if possible
        const historyPromises = apiKeys.map(async (apiKey) => {
            const resolvedKey = keyMapping.get(apiKey) || apiKey;
            
            // For history table, we still need to scan but can optimize
            const scanParams = {
                TableName: historyCostDynamoTableName,
                FilterExpression: '(contains(#accountInfo, :apiKey) OR contains(#accountInfo, :resolvedKey)) AND attribute_exists(#accountInfo)',
                ExpressionAttributeNames: {
                    '#accountInfo': 'accountInfo'
                },
                ExpressionAttributeValues: {
                    ':apiKey': `#${apiKey}`,
                    ':resolvedKey': `#${resolvedKey}`
                },
                ProjectionExpression: 'accountInfo, dailyCost, monthlyCost, userDate, #time',
                ExpressionAttributeNames: {
                    '#accountInfo': 'accountInfo',
                    '#time': 'time'
                }
            };

            let allItems = [];
            let lastEvaluatedKey = null;
            
            do {
                if (lastEvaluatedKey) {
                    scanParams.ExclusiveStartKey = lastEvaluatedKey;
                }
                
                const result = await dynamoDB.send(new ScanCommand(scanParams));
                allItems = allItems.concat(result.Items || []);
                lastEvaluatedKey = result.LastEvaluatedKey;
            } while (lastEvaluatedKey);

            return { apiKey, historyRecords: allItems };
        });

        // Wait for all queries to complete
        const [currentCostResults, historyResults] = await Promise.all([
            Promise.all(currentCostPromises),
            Promise.all(historyPromises)
        ]);

        // Aggregate results
        const results = {};
        
        apiKeys.forEach((apiKey, index) => {
            const currentData = currentCostResults[index];
            const historyData = historyResults[index];
            
            let totalApiKeyCost = 0;
            let userApiKeyCost = 0;
            let timestamps = [];
            
            // Process current cost records
            currentData.records.forEach(record => {
                const dailyCost = parseFloat(record.dailyCost) || 0;
                const monthlyCost = parseFloat(record.monthlyCost) || 0;
                totalApiKeyCost += dailyCost + monthlyCost;
                userApiKeyCost += dailyCost + monthlyCost;
            });
            
            // Process history records
            historyData.historyRecords.forEach(record => {
                const dailyCost = parseFloat(record.dailyCost) || 0;
                const monthlyCost = parseFloat(record.monthlyCost) || 0;
                const cost = dailyCost + monthlyCost;
                
                totalApiKeyCost += cost;
                
                if (record.userDate?.startsWith(email)) {
                    userApiKeyCost += cost;
                }
                
                if (record.time) {
                    timestamps.push(record.time);
                }
            });
            
            results[apiKey] = {
                totalApiKeyCost,
                userApiKeyCost,
                resolvedApiKeyId: keyMapping.get(apiKey),
                recordCount: currentData.records.length + historyData.historyRecords.length,
                latestTimestamp: timestamps.length > 0 ? 
                    timestamps.sort().reverse()[0] : null
            };
        });

        return {
            statusCode: 200,
            body: JSON.stringify({
                email,
                results,
                timestamp: new Date().toISOString()
            }),
        };
    } catch (error) {
        logger.error("Error processing request:", error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

const internalListAllUserMtdCostsHandler = async (event, context, callback) => {
    const startTime = Date.now();
    logger.info("=== LIST ALL USER MTD COSTS REQUEST STARTED ===");
    
    let user = 'unknown'; // Declare user outside try block for catch block access
    
    try {
        logger.info("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params; // This is an error response from extractParams
        }

        const { body } = params;
        user = params.user; // Assign user from params
        logger.info("Request initiated by user", { user, requestBody: body });

        // Check if user is in Admin group by querying ADMIN_DYNAMODB_TABLE
        logger.info("Starting admin privilege verification");
        const adminTableName = process.env.ADMIN_DYNAMODB_TABLE;
        if (!adminTableName) {
            logger.error("ADMIN_DYNAMODB_TABLE environment variable is not set");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        try {
            const adminParams = {
                TableName: adminTableName,
                Key: {
                    config_id: { S: 'admins' }
                }
            };

            logger.info("Admin verification query parameters", { 
                params: adminParams,
                usingRawClient: true 
            });

            const adminCommand = new GetItemCommand(adminParams);
            const adminResult = await client.send(adminCommand);

            logger.info("Admin verification query result", {
                hasItem: !!adminResult.Item,
                itemKeys: adminResult.Item ? Object.keys(adminResult.Item) : null
            });

            if (!adminResult.Item) {
                logger.error("No admin configuration found");
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Admin configuration not found' }),
                };
            }

            const adminEmails = adminResult.Item.data?.L || [];
            logger.info("Admin emails processing", {
                hasData: !!adminResult.Item.data,
                hasL: !!(adminResult.Item.data && adminResult.Item.data.L),
                emailCount: adminEmails.length
            });

            const isAdmin = adminEmails.some(emailObj => 
                emailObj.S && emailObj.S.toLowerCase() === user.toLowerCase()
            );

            logger.info("Admin privilege check result", { 
                user, 
                isAdmin,
                adminEmailsFound: adminEmails.length 
            });

            if (!isAdmin) {
                logger.warn("Unauthorized access attempt to admin endpoint", { user, isAdmin });
                return {
                    statusCode: 403,
                    body: JSON.stringify({ error: 'Access denied. Admin privileges required.' }),
                };
            }
            
            logger.info("Admin privileges verified successfully", { user });
        } catch (error) {
            logger.error("Error verifying admin privileges", { error: error.message, user });
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Authorization check failed' }),
            };
        }

        // Extract pagination parameters with optimized defaults for auto-loading
        const pageSize = body?.data?.pageSize || 100;
        const lastEvaluatedKey = body?.data?.lastEvaluatedKey || null;
        
        logger.info("Pagination parameters", { pageSize, hasLastEvaluatedKey: !!lastEvaluatedKey });

        if (pageSize > 500) {
            logger.warn("Page size too large", { pageSize });
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Page size cannot exceed 500' }),
            };
        }

        // Try GSI first, if no results check if we need to backfill
        logger.info("Starting cost data retrieval from GSI");
        let result;
        let needsBackfill = false;
        const gsiStartTime = Date.now();

        try {
            // Query the GSI to get all cost records efficiently
            const queryParams = {
                TableName: costDynamoTableName,
                IndexName: 'record-type-user-index',
                KeyConditionExpression: 'record_type = :type',
                ExpressionAttributeValues: {
                    ':type': 'cost'
                },
                Limit: Math.min(pageSize * 15, 5000), // Optimized: Get more records to aggregate users efficiently
            };

            if (lastEvaluatedKey) {
                queryParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            logger.info("Querying GSI", { 
                tableName: costDynamoTableName, 
                indexName: 'record-type-user-index',
                limit: queryParams.Limit,
                hasPaginationKey: !!lastEvaluatedKey
            });
            
            const queryCommand = new QueryCommand(queryParams);
            result = await dynamoDB.send(queryCommand);
            
            const gsiDuration = Date.now() - gsiStartTime;
            logger.info("GSI query completed", { 
                itemsFound: result.Items?.length || 0,
                duration: gsiDuration,
                hasNextPage: !!result.LastEvaluatedKey
            });
            
            // If no items found in GSI, check if there are records without record_type
            if (!result.Items || result.Items.length === 0) {
                logger.info("No records found in GSI, checking for legacy records without record_type");
                
                // Quick scan to see if there are any records at all
                const checkScanParams = {
                    TableName: costDynamoTableName,
                    Limit: 1 // Just check if any records exist
                };

                const checkScanCommand = new ScanCommand(checkScanParams);
                const checkResult = await dynamoDB.send(checkScanCommand);
                
                if (checkResult.Items && checkResult.Items.length > 0) {
                    needsBackfill = true;
                    logger.warn("Found legacy records without record_type, backfill needed", {
                        legacyRecordsFound: checkResult.Items.length
                    });
                } else {
                    logger.info("No cost records found in database at all");
                }
            }
        } catch (error) {
            logger.error("Error querying GSI", { 
                error: error.message, 
                tableName: costDynamoTableName,
                indexName: 'record-type-user-index'
            });
            needsBackfill = true;
        }

        // Auto-trigger backfill if needed (admin already verified)
        if (needsBackfill) {
            logger.info("Triggering automatic backfill for record_type field");
            const backfillStartTime = Date.now();
            
            try {
                // Import and run backfill function directly
                const { handler: backfillHandler } = await import('./backfill.js');
                const backfillResult = await backfillHandler({}, {});
                
                const backfillDuration = Date.now() - backfillStartTime;
                logger.info("Backfill completed successfully", { 
                    duration: backfillDuration,
                    result: backfillResult 
                });
                
                // Now retry the GSI query
                logger.info("Retrying GSI query after backfill");
                const retryQueryParams = {
                    TableName: costDynamoTableName,
                    IndexName: 'record-type-user-index',
                    KeyConditionExpression: 'record_type = :type',
                    ExpressionAttributeValues: {
                        ':type': 'cost'
                    },
                    Limit: pageSize * 10,
                };

                if (lastEvaluatedKey) {
                    retryQueryParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                const retryQueryCommand = new QueryCommand(retryQueryParams);
                result = await dynamoDB.send(retryQueryCommand);
                
                logger.info("Post-backfill GSI query completed", { 
                    itemsFound: result.Items?.length || 0 
                });
                
            } catch (backfillError) {
                logger.error("Auto-backfill failed, falling back to table scan", { 
                    error: backfillError.message,
                    backfillDuration: Date.now() - backfillStartTime
                });
                
                // Fallback to scan if backfill fails
                const fallbackScanParams = {
                    TableName: costDynamoTableName,
                    Limit: pageSize * 10,
                };

                if (lastEvaluatedKey) {
                    fallbackScanParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                logger.info("Executing fallback table scan");
                const fallbackScanCommand = new ScanCommand(fallbackScanParams);
                result = await dynamoDB.send(fallbackScanCommand);
                
                logger.info("Fallback scan completed", { 
                    itemsFound: result.Items?.length || 0 
                });
            }
        }

        // Aggregate costs by user with improved accountInfo handling
        logger.info("Starting cost aggregation by user");
        const aggregationStartTime = Date.now();
        const userCosts = new Map();
        let totalRecordsProcessed = 0;
        
        // Process items and resolve amp- keys
        await Promise.all(result.Items.map(async (item) => {
            const email = item.id;
            let accountInfo = item.accountInfo || 'Unknown Account';
            const dailyCost = parseFloat(item.dailyCost) || 0;
            const monthlyCost = parseFloat(item.monthlyCost) || 0;
            const totalCost = dailyCost + monthlyCost;
            
            // Process accountInfo with purpose-based naming (optimized)
            const originalAccountInfo = accountInfo;
            accountInfo = await processAccountInfo(accountInfo);
            
            // Debug logging for duplicate issue
            if (originalAccountInfo !== accountInfo) {
                logger.debug(`AccountInfo transformed: "${originalAccountInfo}" -> "${accountInfo}"`);
            }
            
            if (!userCosts.has(email)) {
                userCosts.set(email, {
                    email,
                    dailyCost: 0,
                    monthlyCost: 0,
                    totalCost: 0,
                    accountsMap: new Map(), // Use Map for deduplication
                    lastUpdated: null
                });
            }
            
            const userCost = userCosts.get(email);
            userCost.dailyCost += dailyCost;
            userCost.monthlyCost += monthlyCost;
            userCost.totalCost += totalCost;
            
            // Deduplicate by accountInfo - sum costs for identical accountInfo
            if (userCost.accountsMap.has(accountInfo)) {
                const existing = userCost.accountsMap.get(accountInfo);
                existing.dailyCost += dailyCost;
                existing.monthlyCost += monthlyCost;
                existing.totalCost += totalCost;
                // Keep latest timestamp
                if (item.time && (!existing.timestamp || item.time > existing.timestamp)) {
                    existing.timestamp = item.time;
                }
            } else {
                userCost.accountsMap.set(accountInfo, {
                    accountInfo,
                    dailyCost,
                    monthlyCost,
                    totalCost,
                    timestamp: item.time || null
                });
            }
            
            // Track latest update
            if (item.time && (!userCost.lastUpdated || item.time > userCost.lastUpdated)) {
                userCost.lastUpdated = item.time;
            }
            
            totalRecordsProcessed++;
        }));

        // Convert to array and sort by total cost descending
        const userCostArray = Array.from(userCosts.values())
            .map(user => ({
                email: user.email,
                dailyCost: user.dailyCost,
                monthlyCost: user.monthlyCost,
                totalCost: user.totalCost,
                accounts: Array.from(user.accountsMap.values()).sort((a, b) => b.totalCost - a.totalCost),
                lastUpdated: user.lastUpdated
            }))
            .sort((a, b) => b.totalCost - a.totalCost);
        
        const aggregationDuration = Date.now() - aggregationStartTime;
        logger.info("Cost aggregation completed", { 
            recordsProcessed: totalRecordsProcessed,
            uniqueUsers: userCostArray.length,
            aggregationDuration,
            topUserCost: userCostArray[0]?.totalCost || 0
        });

        const totalDuration = Date.now() - startTime;
        
        const response = {
            statusCode: 200,
            body: JSON.stringify({
                users: userCostArray,
                count: userCostArray.length,
                lastEvaluatedKey: result.LastEvaluatedKey || null,
                hasMore: !!result.LastEvaluatedKey
            }),
        };
        
        logger.info("=== LIST ALL USER MTD COSTS REQUEST COMPLETED ===", {
            totalDuration,
            usersReturned: userCostArray.length,
            recordsProcessed: totalRecordsProcessed,
            hasMoreData: !!result.LastEvaluatedKey,
            requestedBy: user
        });
        
        return response;
    } catch (error) {
        const totalDuration = Date.now() - startTime;
        logger.error("=== LIST ALL USER MTD COSTS REQUEST FAILED ===", { 
            error: error.message, 
            stack: error.stack,
            totalDuration,
            requestedBy: user || 'unknown'
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

const internalBillingGroupsCostsHandler = async (event, context, callback) => {
    const startTime = Date.now();
    logger.info("=== BILLING GROUPS COSTS REQUEST STARTED ===");
    
    let user = 'unknown'; // Declare user outside try block for catch block access
    
    try {
        logger.info("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params;
        }

        const { body } = params;
        user = params.user; // Assign user from params
        logger.info("Request initiated by user", { user, requestBody: body });

        // Step 1: Check admin privileges
        logger.info("Verifying admin privileges");
        const adminTableName = process.env.ADMIN_DYNAMODB_TABLE;
        if (!adminTableName) {
            logger.error("ADMIN_DYNAMODB_TABLE environment variable is not set");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        try {
            const adminParams = {
                TableName: adminTableName,
                Key: {
                    config_id: { S: 'admins' }
                }
            };

            const adminCommand = new GetItemCommand(adminParams);
            const adminResult = await client.send(adminCommand);

            if (!adminResult.Item) {
                logger.error("No admin configuration found");
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Admin configuration not found' }),
                };
            }

            const adminEmails = adminResult.Item.data?.L || [];
            const isAdmin = adminEmails.some(emailObj => 
                emailObj.S && emailObj.S.toLowerCase() === user.toLowerCase()
            );

            if (!isAdmin) {
                logger.warn("Unauthorized access attempt", { user });
                return {
                    statusCode: 403,
                    body: JSON.stringify({ error: 'Access denied. Admin privileges required.' }),
                };
            }
            
            logger.info("Admin privileges verified", { user });
        } catch (error) {
            logger.error("Error verifying admin privileges", { error: error.message });
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Authorization check failed' }),
            };
        }

        // Step 2: Fetch all amplify groups
        logger.info("Fetching amplify groups configuration");
        
        // Log the table name and parameters
        logger.info("Admin table configuration", { 
            adminTableName,
            tableExists: !!adminTableName 
        });
        
        let allGroups; // Declare outside try-catch for accessibility
        
        const groupsParams = {
            TableName: adminTableName,
            Key: {
                config_id: 'amplifyGroups'
            }
        };

        logger.info("Groups query parameters", { 
            params: groupsParams,
            keyStructure: Object.keys(groupsParams.Key),
            keyValues: groupsParams.Key
        });

        try {
            const groupsCommand = new GetItemCommand(groupsParams);
            logger.info("Sending groups query command with DynamoDBDocumentClient");
            
            const groupsResult = await dynamoDB.send(groupsCommand);
            
            logger.info("Groups query result received", { 
                hasItem: !!groupsResult.Item,
                itemKeys: groupsResult.Item ? Object.keys(groupsResult.Item) : null,
                hasData: !!(groupsResult.Item && groupsResult.Item.data),
                dataType: groupsResult.Item && groupsResult.Item.data ? typeof groupsResult.Item.data : null
            });

            if (!groupsResult.Item || !groupsResult.Item.data) {
                logger.info("No amplify groups found in result");
                return {
                    statusCode: 200,
                    body: JSON.stringify({ billingGroups: {}, totalUsers: 0, totalCost: 0 }),
                };
            }

            // Parse the groups data
            allGroups = groupsResult.Item.data;
            logger.info("Successfully fetched amplify groups", { 
                groupCount: Object.keys(allGroups).length,
                firstFewGroups: Object.keys(allGroups).slice(0, 3),
                dataStructure: typeof allGroups
            });
            
        } catch (groupsError) {
            logger.error("Error fetching amplify groups", { 
                error: groupsError.message,
                stack: groupsError.stack,
                params: groupsParams
            });
            
            // Try fallback approach with raw DynamoDB client
            logger.info("Attempting fallback with raw DynamoDB client");
            try {
                const fallbackParams = {
                    TableName: adminTableName,
                    Key: {
                        config_id: { S: 'amplifyGroups' }
                    }
                };
                
                logger.info("Fallback query parameters", { params: fallbackParams });
                
                const fallbackCommand = new GetItemCommand(fallbackParams);
                const fallbackResult = await client.send(fallbackCommand);
                
                logger.info("Fallback query result", {
                    hasItem: !!fallbackResult.Item,
                    itemStructure: fallbackResult.Item ? Object.keys(fallbackResult.Item) : null
                });
                
                if (!fallbackResult.Item || !fallbackResult.Item.data) {
                    logger.info("No amplify groups found in fallback result");
                    return {
                        statusCode: 200,
                        body: JSON.stringify({ billingGroups: {}, totalUsers: 0, totalCost: 0 }),
                    };
                }
                
                // For raw DynamoDB result, we need to handle the raw format
                const rawData = fallbackResult.Item.data;
                logger.info("Raw data structure from fallback", {
                    dataType: typeof rawData,
                    hasL: !!(rawData && rawData.L),
                    hasM: !!(rawData && rawData.M),
                    keys: rawData ? Object.keys(rawData) : null
                });
                
                // Handle different possible data structures
                if (rawData && rawData.M) {
                    // Data is a Map type
                    allGroups = rawData.M;
                    logger.info("Using Map structure from raw data");
                } else if (rawData && typeof rawData === 'object') {
                    // Data might already be in usable format
                    allGroups = rawData;
                    logger.info("Using direct object from raw data");
                } else {
                    logger.error("Unexpected data structure in fallback", { rawData });
                    return {
                        statusCode: 500,
                        body: JSON.stringify({ error: 'Unexpected amplify groups data structure' }),
                    };
                }
                
                logger.info("Successfully processed fallback amplify groups", { 
                    groupCount: Object.keys(allGroups).length,
                    firstFewGroups: Object.keys(allGroups).slice(0, 3)
                });
                
            } catch (fallbackError) {
                logger.error("Fallback also failed", {
                    error: fallbackError.message,
                    stack: fallbackError.stack
                });
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Failed to fetch amplify groups configuration' }),
                };
            }
        }

        // Step 3: Build affiliation map for billing groups
        logger.info("Building billing group affiliations");
        const affiliationStartTime = Date.now();
        
        const directMembersMap = {};
        const inclusionMap = {};
        const billingGroups = new Set();
        
        // Build maps in single pass
        for (const [groupName, groupData] of Object.entries(allGroups)) {
            directMembersMap[groupName] = groupData.members || groupData.M?.members?.L?.map(m => m.S) || [];
            inclusionMap[groupName] = groupData.includeFromOtherGroups || 
                                     groupData.M?.includeFromOtherGroups?.L?.map(g => g.S) || [];
            
            // Check if it's a billing group
            const isBilling = groupData.isBillingGroup || groupData.M?.isBillingGroup?.BOOL;
            if (isBilling) {
                billingGroups.add(groupName);
            }
        }

        logger.info("Identified billing groups", { count: billingGroups.size, groups: Array.from(billingGroups) });

        // Build complete affiliation map for billing groups
        const affiliationMap = {};
        const allUniqueUsers = new Set();
        
        for (const billingGroup of billingGroups) {
            const users = new Map(); // email -> { type, via, path }
            const visited = new Set();
            const queue = [{ group: billingGroup, path: [] }];
            
            while (queue.length > 0) {
                const { group, path } = queue.shift();
                
                if (visited.has(group)) continue;
                visited.add(group);
                
                // Add direct members of this group
                const directMembers = directMembersMap[group] || [];
                for (const userEmail of directMembers) {
                    if (!users.has(userEmail)) {
                        users.set(userEmail, {
                            type: group === billingGroup ? 'direct' : 'indirect',
                            via: group === billingGroup ? null : group,
                            path: [...path, group]
                        });
                        allUniqueUsers.add(userEmail);
                    }
                }
                
                // Queue included groups for processing
                const includedGroups = inclusionMap[group] || [];
                for (const includedGroup of includedGroups) {
                    if (!visited.has(includedGroup)) {
                        queue.push({ 
                            group: includedGroup, 
                            path: [...path, group] 
                        });
                    }
                }
            }
            
            affiliationMap[billingGroup] = {
                users: users,
                summary: {
                    directCount: Array.from(users.values()).filter(u => u.type === 'direct').length,
                    indirectCount: Array.from(users.values()).filter(u => u.type === 'indirect').length,
                    totalCount: users.size
                }
            };
        }
        
        const affiliationDuration = Date.now() - affiliationStartTime;
        logger.info("Affiliation map built", { 
            duration: affiliationDuration,
            uniqueUsers: allUniqueUsers.size,
            billingGroups: billingGroups.size
        });

        // Step 4: Query costs for all unique users
        if (allUniqueUsers.size === 0) {
            logger.info("No users found in billing groups");
            return {
                statusCode: 200,
                body: JSON.stringify({ billingGroups: {}, totalUsers: 0, totalCost: 0 }),
            };
        }

        logger.info("Querying costs for all users", { userCount: allUniqueUsers.size });
        const costQueryStartTime = Date.now();
        
        // Batch query costs for all users
        const userCostsMap = new Map();
        const userArray = Array.from(allUniqueUsers);
        
        // Query in batches of 25 users (DynamoDB batch limit)
        const batchSize = 25;
        for (let i = 0; i < userArray.length; i += batchSize) {
            const batch = userArray.slice(i, i + batchSize);
            
            // Query costs for this batch of users
            const batchPromises = batch.map(async (email) => {
                const queryParams = {
                    TableName: costDynamoTableName,
                    KeyConditionExpression: 'id = :email',
                    ExpressionAttributeValues: {
                        ':email': email,
                    },
                };

                try {
                    const result = await dynamoDB.send(new QueryCommand(queryParams));
                    
                    let dailyCost = 0;
                    let monthlyCost = 0;
                    const accountsMap = new Map(); // Use Map for deduplication
                    
                    if (result.Items && result.Items.length > 0) {
                        for (const item of result.Items) {
                            let accountInfo = item.accountInfo || 'Unknown Account';
                            const itemDailyCost = parseFloat(item.dailyCost) || 0;
                            const itemMonthlyCost = parseFloat(item.monthlyCost) || 0;
                            
                            // Process accountInfo with purpose-based naming (optimized)
                            accountInfo = await processAccountInfo(accountInfo);
                            
                            dailyCost += itemDailyCost;
                            monthlyCost += itemMonthlyCost;
                            
                            // Deduplicate by accountInfo - sum costs for identical accountInfo
                            if (accountsMap.has(accountInfo)) {
                                const existing = accountsMap.get(accountInfo);
                                existing.dailyCost += itemDailyCost;
                                existing.monthlyCost += itemMonthlyCost;
                                existing.totalCost += itemDailyCost + itemMonthlyCost;
                            } else {
                                accountsMap.set(accountInfo, {
                                    accountInfo,
                                    dailyCost: itemDailyCost,
                                    monthlyCost: itemMonthlyCost,
                                    totalCost: itemDailyCost + itemMonthlyCost
                                });
                            }
                        }
                    }
                    
                    // Convert accountsMap to array
                    const accounts = Array.from(accountsMap.values()).sort((a, b) => b.totalCost - a.totalCost);
                    
                    userCostsMap.set(email, {
                        email,
                        dailyCost,
                        monthlyCost,
                        totalCost: dailyCost + monthlyCost,
                        accounts
                    });
                } catch (error) {
                    logger.warn(`Failed to get costs for user ${email}`, { error: error.message });
                    userCostsMap.set(email, {
                        email,
                        dailyCost: 0,
                        monthlyCost: 0,
                        totalCost: 0,
                        accounts: []
                    });
                }
            });
            
            await Promise.all(batchPromises);
        }
        
        const costQueryDuration = Date.now() - costQueryStartTime;
        logger.info("Cost queries completed", { 
            duration: costQueryDuration,
            usersQueried: userCostsMap.size
        });

        // Step 5: Build final response with costs distributed to billing groups
        logger.info("Building final billing groups response");
        const billingGroupsData = {};
        let platformTotalCost = 0;
        
        for (const [groupName, affiliation] of Object.entries(affiliationMap)) {
            const groupUsers = [];
            let groupTotalCost = 0;
            let groupDailyCost = 0;
            let groupMonthlyCost = 0;
            
            // Process each user in this billing group
            for (const [userEmail, userMembership] of affiliation.users.entries()) {
                const userCost = userCostsMap.get(userEmail) || {
                    email: userEmail,
                    dailyCost: 0,
                    monthlyCost: 0,
                    totalCost: 0,
                    accounts: []
                };
                
                groupUsers.push({
                    ...userCost,
                    membershipType: userMembership.type,
                    via: userMembership.via,
                    path: userMembership.path
                });
                
                groupTotalCost += userCost.totalCost;
                groupDailyCost += userCost.dailyCost;
                groupMonthlyCost += userCost.monthlyCost;
            }
            
            // Sort users by cost (highest first)
            groupUsers.sort((a, b) => b.totalCost - a.totalCost);
            
            // Get group configuration
            const groupConfig = allGroups[groupName];
            
            billingGroupsData[groupName] = {
                groupInfo: {
                    name: groupName,
                    createdBy: groupConfig.createdBy || groupConfig.M?.createdBy?.S || 'Unknown',
                    rateLimit: groupConfig.rateLimit || groupConfig.M?.rateLimit || null,
                    directMemberCount: affiliation.summary.directCount,
                    indirectMemberCount: affiliation.summary.indirectCount,
                    totalMemberCount: affiliation.summary.totalCount
                },
                costs: {
                    total: groupTotalCost,
                    daily: groupDailyCost,
                    monthly: groupMonthlyCost,
                    avgPerMember: affiliation.summary.totalCount > 0 ? 
                        groupTotalCost / affiliation.summary.totalCount : 0
                },
                members: {
                    all: groupUsers,
                    direct: groupUsers.filter(u => u.membershipType === 'direct'),
                    indirect: groupUsers.filter(u => u.membershipType === 'indirect'),
                    topSpenders: groupUsers.slice(0, 5)
                }
            };
            
            platformTotalCost += groupTotalCost;
        }
        
        // Calculate percentage of platform costs for each group
        for (const groupData of Object.values(billingGroupsData)) {
            groupData.costs.percentOfPlatform = platformTotalCost > 0 ? 
                (groupData.costs.total / platformTotalCost) * 100 : 0;
        }
        
        const totalDuration = Date.now() - startTime;
        
        const response = {
            statusCode: 200,
            body: JSON.stringify({
                billingGroups: billingGroupsData,
                summary: {
                    totalBillingGroups: billingGroups.size,
                    totalUsers: allUniqueUsers.size,
                    totalCost: platformTotalCost,
                    timestamp: new Date().toISOString()
                }
            }),
        };
        
        logger.info("=== BILLING GROUPS COSTS REQUEST COMPLETED ===", {
            totalDuration,
            billingGroups: billingGroups.size,
            totalUsers: allUniqueUsers.size,
            totalCost: platformTotalCost,
            requestedBy: user
        });
        
        return response;
        
    } catch (error) {
        const totalDuration = Date.now() - startTime;
        logger.error("=== BILLING GROUPS COSTS REQUEST FAILED ===", { 
            error: error.message, 
            stack: error.stack,
            totalDuration,
            requestedBy: user || 'unknown'
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

const internalListUserMtdCostsHandler = async (event, context, callback) => {
    const startTime = Date.now();
    logger.info("=== LIST USER MTD COSTS REQUEST STARTED ===");
    
    let user = 'unknown'; // Declare user outside try block for catch block access
    
    try {
        logger.info("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params; // This is an error response from extractParams
        }

        const { body } = params;
        user = params.user; // Assign user from params
        logger.info("Request initiated by user", { user, requestBody: body });

        // No admin check needed - users can access their own data
        logger.info("Starting cost data retrieval for user", { user });
        
        // Query the cost table directly for this specific user
        const queryParams = {
            TableName: costDynamoTableName,
            KeyConditionExpression: 'id = :email',
            ExpressionAttributeValues: {
                ':email': user,
            },
        };

        logger.info("Querying cost table for user", { 
            tableName: costDynamoTableName, 
            user: user
        });

        const queryCommand = new QueryCommand(queryParams);
        const result = await dynamoDB.send(queryCommand);
        
        logger.info("User cost query completed", { 
            itemsFound: result.Items?.length || 0
        });

        if (!result.Items || result.Items.length === 0) {
            logger.info("No cost data found for user", { user });
            return {
                statusCode: 200,
                body: JSON.stringify({ 
                    email: user,
                    dailyCost: 0,
                    monthlyCost: 0,
                    totalCost: 0,
                    accounts: [],
                    lastUpdated: null,
                    timestamp: new Date().toISOString()
                }),
            };
        }

        // Aggregate costs for this user with improved handling
        logger.info("Starting cost aggregation for user");
        const aggregationStartTime = Date.now();
        
        let totalDailyCost = 0;
        let totalMonthlyCost = 0;
        const accountsMap = new Map(); // Use Map for deduplication
        let lastUpdated = null;
        
        // Get systemKey costs for this user from other system users
        const systemKeyCosts = await getSystemKeyCostsForUser(user);
        
        // Process items and resolve amp- keys
        await Promise.all(result.Items.map(async (item) => {
            let accountInfo = item.accountInfo || 'Unknown Account';
            const dailyCost = parseFloat(item.dailyCost) || 0;
            const monthlyCost = parseFloat(item.monthlyCost) || 0;
            
            // Process accountInfo with purpose-based naming (optimized)
            accountInfo = await processAccountInfo(accountInfo);
            
            totalDailyCost += dailyCost;
            totalMonthlyCost += monthlyCost;
            
            // Deduplicate by accountInfo - sum costs for identical accountInfo
            if (accountsMap.has(accountInfo)) {
                const existing = accountsMap.get(accountInfo);
                existing.dailyCost += dailyCost;
                existing.monthlyCost += monthlyCost;
                existing.totalCost += dailyCost + monthlyCost;
                // Keep latest timestamp
                if (item.time && (!existing.timestamp || item.time > existing.timestamp)) {
                    existing.timestamp = item.time;
                }
            } else {
                accountsMap.set(accountInfo, {
                    accountInfo,
                    dailyCost,
                    monthlyCost,
                    totalCost: dailyCost + monthlyCost,
                    timestamp: item.time || null
                });
            }
            
            // Track latest update
            if (item.time && (!lastUpdated || item.time > lastUpdated)) {
                lastUpdated = item.time;
            }
        }));
        
        // Add systemKey costs from other system users
        for (const systemCost of systemKeyCosts) {
            const dailyCost = parseFloat(systemCost.dailyCost) || 0;
            const monthlyCost = parseFloat(systemCost.monthlyCost) || 0;
            
            totalDailyCost += dailyCost;
            totalMonthlyCost += monthlyCost;
            
            // Process the systemKey accountInfo
            let accountInfo = await processAccountInfo(systemCost.accountInfo);
            
            // Deduplicate systemKey costs as well
            if (accountsMap.has(accountInfo)) {
                const existing = accountsMap.get(accountInfo);
                existing.dailyCost += dailyCost;
                existing.monthlyCost += monthlyCost;
                existing.totalCost += dailyCost + monthlyCost;
                // Keep latest timestamp
                if (systemCost.time && (!existing.timestamp || systemCost.time > existing.timestamp)) {
                    existing.timestamp = systemCost.time;
                }
            } else {
                accountsMap.set(accountInfo, {
                    accountInfo,
                    dailyCost,
                    monthlyCost,
                    totalCost: dailyCost + monthlyCost,
                    timestamp: systemCost.time || null
                });
            }
            
            // Track latest update
            if (systemCost.time && (!lastUpdated || systemCost.time > lastUpdated)) {
                lastUpdated = systemCost.time;
            }
        }

        const totalCost = totalDailyCost + totalMonthlyCost;
        
        // Convert accountsMap to array and sort by total cost descending
        const accounts = Array.from(accountsMap.values()).sort((a, b) => b.totalCost - a.totalCost);
        
        const aggregationDuration = Date.now() - aggregationStartTime;
        logger.info("Cost aggregation completed", { 
            recordsProcessed: result.Items.length,
            totalCost: totalCost,
            aggregationDuration
        });

        const userCostData = {
            email: user,
            dailyCost: totalDailyCost,
            monthlyCost: totalMonthlyCost,
            totalCost: totalCost,
            accounts: accounts,
            lastUpdated,
            timestamp: new Date().toISOString()
        };

        const totalDuration = Date.now() - startTime;
        
        const response = {
            statusCode: 200,
            body: JSON.stringify(userCostData),
        };
        
        logger.info("=== LIST USER MTD COSTS REQUEST COMPLETED ===", {
            totalDuration,
            accountsCount: accounts.length,
            totalCost: totalCost,
            requestedBy: user
        });
        
        return response;
        
    } catch (error) {
        const totalDuration = Date.now() - startTime;
        logger.error("=== LIST USER MTD COSTS REQUEST FAILED ===", { 
            error: error.message, 
            stack: error.stack,
            totalDuration,
            requestedBy: user || 'unknown'
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

const internalGetUserCostHistoryHandler = async (event, context, callback) => {
    const startTime = Date.now();
    logger.info("=== GET USER COST HISTORY REQUEST STARTED ===");
    
    let user = 'unknown';
    
    try {
        logger.info("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params;
        }

        const { body } = params;
        user = params.user;
        const requestedEmail = body?.data?.email || user;
        const monthsBack = body?.data?.monthsBack || 12;
        
        logger.info("Request initiated by user", { user, requestedEmail, monthsBack });

        // Check admin privileges if requesting another user's history
        if (requestedEmail !== user) {
            logger.info("Verifying admin privileges for cross-user history request");
            const adminTableName = process.env.ADMIN_DYNAMODB_TABLE;
            
            if (!adminTableName) {
                logger.error("ADMIN_DYNAMODB_TABLE environment variable is not set");
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Server configuration error' }),
                };
            }

            try {
                const adminParams = {
                    TableName: adminTableName,
                    Key: {
                        config_id: { S: 'admins' }
                    }
                };

                const adminCommand = new GetItemCommand(adminParams);
                const adminResult = await client.send(adminCommand);

                if (!adminResult.Item) {
                    logger.error("No admin configuration found");
                    return {
                        statusCode: 500,
                        body: JSON.stringify({ error: 'Admin configuration not found' }),
                    };
                }

                const adminEmails = adminResult.Item.data?.L || [];
                const isAdmin = adminEmails.some(emailObj => 
                    emailObj.S && emailObj.S.toLowerCase() === user.toLowerCase()
                );

                if (!isAdmin) {
                    logger.warn("Unauthorized access attempt to user history", { user, requestedEmail });
                    return {
                        statusCode: 403,
                        body: JSON.stringify({ error: 'Access denied. Admin privileges required.' }),
                    };
                }
                
                logger.info("Admin privileges verified", { user });
            } catch (error) {
                logger.error("Error verifying admin privileges", { error: error.message });
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Authorization check failed' }),
                };
            }
        }

        // Calculate date range
        const endDate = new Date();
        const startDate = new Date();
        startDate.setMonth(startDate.getMonth() - monthsBack);
        
        const startMonth = startDate.toISOString().slice(0, 7);
        const endMonth = endDate.toISOString().slice(0, 7);
        
        logger.info("Querying history table", { 
            requestedEmail, 
            startMonth, 
            endMonth,
            tableName: historyCostDynamoTableName 
        });

        // Query history table using Scan with filter (since we need to match userDate prefix)
        const scanParams = {
            TableName: historyCostDynamoTableName,
            FilterExpression: 'begins_with(userDate, :emailPrefix)',
            ExpressionAttributeValues: {
                ':emailPrefix': `${requestedEmail}#`
            }
        };

        let allHistoryItems = [];
        let lastEvaluatedKey = null;

        do {
            if (lastEvaluatedKey) {
                scanParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            const scanCommand = new ScanCommand(scanParams);
            const result = await dynamoDB.send(scanCommand);
            
            if (result.Items && result.Items.length > 0) {
                allHistoryItems = allHistoryItems.concat(result.Items);
            }
            
            lastEvaluatedKey = result.LastEvaluatedKey;
        } while (lastEvaluatedKey);

        logger.info("History records retrieved", { count: allHistoryItems.length });

        // Group by month and aggregate
        const monthlyData = {};
        
        for (const item of allHistoryItems) {
            const userDate = item.userDate || '';
            const datePart = userDate.split('#')[1];
            
            if (!datePart) continue;
            
            // Extract month (YYYY-MM or YYYY-MM-DD)
            const month = datePart.length === 7 ? datePart : datePart.slice(0, 7);
            
            if (!monthlyData[month]) {
                monthlyData[month] = {
                    month,
                    dailyCostSum: 0,
                    monthlyCostSum: 0,
                    totalCost: 0,
                    accountsMap: new Map(),
                    daysInMonth: new Set()
                };
            }
            
            const accountInfo = await processAccountInfo(item.accountInfo || 'Unknown Account');
            const dailyCost = parseFloat(item.dailyCost) || 0;
            const monthlyCost = parseFloat(item.monthlyCost) || 0;
            const cost = dailyCost + monthlyCost;
            
            monthlyData[month].dailyCostSum += dailyCost;
            monthlyData[month].monthlyCostSum += monthlyCost;
            monthlyData[month].totalCost += cost;
            
            // Track unique days
            if (datePart.length === 10) {
                monthlyData[month].daysInMonth.add(datePart);
            }
            
            // Aggregate by account
            if (monthlyData[month].accountsMap.has(accountInfo)) {
                monthlyData[month].accountsMap.set(
                    accountInfo,
                    monthlyData[month].accountsMap.get(accountInfo) + cost
                );
            } else {
                monthlyData[month].accountsMap.set(accountInfo, cost);
            }
        }

        // Convert to array and sort by date (newest first)
        const history = Object.values(monthlyData)
            .map(monthData => ({
                month: monthData.month,
                displayMonth: new Date(monthData.month + '-01').toLocaleDateString('en-US', { 
                    year: 'numeric', 
                    month: 'short' 
                }),
                totalCost: monthData.totalCost,
                dailyCostSum: monthData.dailyCostSum,
                monthlyCostSum: monthData.monthlyCostSum,
                accounts: Array.from(monthData.accountsMap.entries())
                    .map(([accountInfo, cost]) => ({ accountInfo, cost }))
                    .sort((a, b) => b.cost - a.cost),
                daysInMonth: monthData.daysInMonth.size,
                isCurrent: monthData.month === endMonth
            }))
            .sort((a, b) => b.month.localeCompare(a.month));

        // Calculate summary statistics
        const totalSpendAllTime = history.reduce((sum, m) => sum + m.totalCost, 0);
        const avgMonthlySpend = history.length > 0 ? totalSpendAllTime / history.length : 0;
        
        let trend = { direction: 'flat', percentage: 0, comparison: '' };
        if (history.length >= 2) {
            const current = history[0].totalCost;
            const previous = history[1].totalCost;
            
            if (previous > 0) {
                const change = ((current - previous) / previous) * 100;
                trend = {
                    direction: change > 0 ? 'up' : change < 0 ? 'down' : 'flat',
                    percentage: Math.abs(change),
                    comparison: `${history[0].displayMonth}: ${formatCurrency(current)} vs ${history[1].displayMonth}: ${formatCurrency(previous)}`
                };
            }
        }

        const response = {
            statusCode: 200,
            body: JSON.stringify({
                email: requestedEmail,
                history,
                summary: {
                    totalSpendAllTime,
                    avgMonthlySpend,
                    monthCount: history.length,
                    firstMonth: history.length > 0 ? history[history.length - 1].month : null,
                    lastMonth: history.length > 0 ? history[0].month : null,
                    trend
                }
            }),
        };

        const totalDuration = Date.now() - startTime;
        logger.info("=== GET USER COST HISTORY REQUEST COMPLETED ===", {
            totalDuration,
            requestedEmail,
            monthsReturned: history.length,
            totalRecordsProcessed: allHistoryItems.length,
            requestedBy: user
        });

        return response;

    } catch (error) {
        const totalDuration = Date.now() - startTime;
        logger.error("=== GET USER COST HISTORY REQUEST FAILED ===", { 
            error: error.message, 
            stack: error.stack,
            totalDuration,
            requestedBy: user
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

// Helper function for currency formatting (if not already defined)
const formatCurrency = (amount) => {
    return `$${amount.toFixed(2)}`;
};



export const handler = mtdHandler;
export const apiKeyUserCostHandler = internalApiKeyUserCostHandler;
export const listAllUserMtdCostsHandler = internalListAllUserMtdCostsHandler;
export const billingGroupsCostsHandler = internalBillingGroupsCostsHandler;
export const listUserMtdCostsHandler = internalListUserMtdCostsHandler;