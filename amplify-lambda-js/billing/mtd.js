import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, ScanCommand } from "@aws-sdk/lib-dynamodb";
import { extractParams } from "../common/handlers.js";
import { getLogger } from "../common/logging.js";

const logger = getLogger("mtd");
const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyCostDynamoTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

export const handler = async (event, context, callback) => {
    try {
        logger.debug("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            return params; // This is an error response from extractParams
        }

        const { body } = params;

        if (!body || !body.data || !body.data.email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Email is required' }),
            };
        }

        const email = body.data.email;

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
                statusCode: 404,
                body: JSON.stringify({ error: 'No cost data found for the given email' }),
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

export const apiKeyUserCostHandler = async (event, context, callback) => {
    try {
        const params = await extractParams(event);
        if (params.statusCode) return params;

        const { body } = params;
        if (!body || !body.data || !Array.isArray(body.data.apiKeys) || !body.data.email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'API keys array and email are required' }),
            };
        }

        const apiKeys = body.data.apiKeys;
        const email = body.data.email;

        if (!historyCostDynamoTableName || !costDynamoTableName || !email || apiKeys.length === 0) {
            logger.error("Missing required parameters");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        let results = {};

        for (const apiKey of apiKeys) {
            let totalApiKeyCost = 0;
            let userApiKeyCost = 0;
            let lastEvaluatedKey = null;

            // Query historyCostDynamoTableName
            do {
                const scanParams = {
                    TableName: historyCostDynamoTableName,
                    FilterExpression: 'contains(#accountInfo, :apiKey) AND attribute_exists(#accountInfo)',
                    ExpressionAttributeNames: {
                        '#accountInfo': 'accountInfo'
                    },
                    ExpressionAttributeValues: {
                        ':apiKey': apiKey
                    }
                };

                if (lastEvaluatedKey) {
                    scanParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                try {
                    const command = new ScanCommand(scanParams);
                    const result = await dynamoDB.send(command);

                    result.Items.forEach(item => {
                        const dailyCost = parseFloat(item.dailyCost) || 0;
                        totalApiKeyCost += dailyCost;

                        if (item.userDate && item.userDate.startsWith(email)) {
                            userApiKeyCost += dailyCost;
                        }
                    });

                    lastEvaluatedKey = result.LastEvaluatedKey;
                } catch (error) {
                    logger.error("Error executing DynamoDB scan:", error);
                    throw error;
                }
            } while (lastEvaluatedKey);

            // Query costDynamoTableName
            const queryParams = {
                TableName: costDynamoTableName,
                KeyConditionExpression: 'id = :email',
                ExpressionAttributeValues: {
                    ':email': email,
                },
            };

            try {
                const command = new QueryCommand(queryParams);
                const result = await dynamoDB.send(command);

                result.Items.forEach(item => {
                    if (item.accountInfo && item.accountInfo.includes(apiKey)) {
                        const dailyCost = parseFloat(item.dailyCost) || 0;
                        totalApiKeyCost += dailyCost;
                        userApiKeyCost += dailyCost;
                    }
                });
            } catch (error) {
                logger.error("Error executing DynamoDB query:", error);
                throw error;
            }

            results[apiKey] = { totalApiKeyCost, userApiKeyCost };
        }

        return {
            statusCode: 200,
            body: JSON.stringify({
                email,
                results,
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

export const listAllUserMtdCostsHandler = async (event, context, callback) => {
    try {
        logger.debug("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            return params; // This is an error response from extractParams
        }

        const { body, user } = params;

        // Check if user is in Admin group by querying ADMIN_DYNAMODB_TABLE
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

            const adminConfig = adminResult.Item;
            const adminEmails = adminConfig.data?.L || [];
            
            // Check if current user's email is in the admin list
            const isAdmin = adminEmails.some(emailObj => 
                emailObj.S && emailObj.S.toLowerCase() === user.toLowerCase()
            );

            if (!isAdmin) {
                logger.warn(`Unauthorized access attempt to admin endpoint by user: ${user}`);
                return {
                    statusCode: 403,
                    body: JSON.stringify({ error: 'Access denied. Admin privileges required.' }),
                };
            }
        } catch (error) {
            logger.error("Error verifying admin privileges:", error);
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Authorization check failed' }),
            };
        }

        // Extract pagination parameters
        const pageSize = body?.data?.pageSize || 50;
        const lastEvaluatedKey = body?.data?.lastEvaluatedKey || null;

        if (pageSize > 100) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Page size cannot exceed 100' }),
            };
        }

        // Try GSI first, if no results check if we need to backfill
        let result;
        let needsBackfill = false;

        try {
            // Query the GSI to get all cost records efficiently
            const queryParams = {
                TableName: costDynamoTableName,
                IndexName: 'record-type-user-index',
                KeyConditionExpression: 'record_type = :type',
                ExpressionAttributeValues: {
                    ':type': 'cost'
                },
                Limit: pageSize * 10, // Get more records to ensure we have enough users after aggregation
            };

            if (lastEvaluatedKey) {
                queryParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            const queryCommand = new QueryCommand(queryParams);
            result = await dynamoDB.send(queryCommand);
            
            // If no items found in GSI, check if there are records without record_type
            if (!result.Items || result.Items.length === 0) {
                logger.info("No records found in GSI, checking for records without record_type");
                
                // Quick scan to see if there are any records at all
                const checkScanParams = {
                    TableName: costDynamoTableName,
                    Limit: 1 // Just check if any records exist
                };

                const checkScanCommand = new ScanCommand(checkScanParams);
                const checkResult = await dynamoDB.send(checkScanCommand);
                
                if (checkResult.Items && checkResult.Items.length > 0) {
                    needsBackfill = true;
                    logger.info("Found records without record_type, will trigger backfill");
                }
            }
        } catch (error) {
            logger.error("Error querying GSI:", error);
            needsBackfill = true;
        }

        // Auto-trigger backfill if needed (admin already verified)
        if (needsBackfill) {
            logger.info("Triggering automatic backfill for record_type field");
            
            try {
                // Import and run backfill function directly
                const { handler: backfillHandler } = await import('./backfill.js');
                const backfillResult = await backfillHandler({}, {});
                
                logger.info("Backfill completed:", JSON.stringify(backfillResult));
                
                // Now retry the GSI query
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
                
            } catch (backfillError) {
                logger.error("Auto-backfill failed, falling back to table scan:", backfillError);
                
                // Fallback to scan if backfill fails
                const fallbackScanParams = {
                    TableName: costDynamoTableName,
                    Limit: pageSize * 10,
                };

                if (lastEvaluatedKey) {
                    fallbackScanParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                const fallbackScanCommand = new ScanCommand(fallbackScanParams);
                result = await dynamoDB.send(fallbackScanCommand);
            }
        }

        // Aggregate costs by user
        const userCosts = {};
        
        result.Items.forEach(item => {
            const email = item.id;
            const accountInfo = item.accountInfo || 'Unknown Account';
            const dailyCost = parseFloat(item.dailyCost) || 0;
            const monthlyCost = parseFloat(item.monthlyCost) || 0;
            
            if (!userCosts[email]) {
                userCosts[email] = {
                    email: email,
                    dailyCost: 0,
                    monthlyCost: 0,
                    totalCost: 0,
                    accounts: []
                };
            }
            
            userCosts[email].dailyCost += dailyCost;
            userCosts[email].monthlyCost += monthlyCost;
            
            // Add account information
            userCosts[email].accounts.push({
                accountInfo: accountInfo,
                dailyCost: dailyCost,
                monthlyCost: monthlyCost,
                totalCost: dailyCost + monthlyCost
            });
        });

        // Calculate total costs for each user
        Object.keys(userCosts).forEach(email => {
            userCosts[email].totalCost = userCosts[email].dailyCost + userCosts[email].monthlyCost;
        });

        // Convert to array and sort by total cost descending
        const userCostArray = Object.values(userCosts).sort((a, b) => b.totalCost - a.totalCost);

        return {
            statusCode: 200,
            body: JSON.stringify({
                users: userCostArray,
                count: userCostArray.length,
                lastEvaluatedKey: result.LastEvaluatedKey || null,
                hasMore: !!result.LastEvaluatedKey
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