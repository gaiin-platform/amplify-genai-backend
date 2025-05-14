import {DynamoDBClient, QueryCommand} from "@aws-sdk/client-dynamodb";
import {unmarshall} from "@aws-sdk/util-dynamodb";
import {getLogger} from "../common/logging.js";

const logger = getLogger("rateLimiter");


export async function isRateLimited(params) {
    const rateLimit = params.body.options.rateLimit;
    const adminRateLimit = await getAdminRateLimit(); 
    const noLimit = (limit) => {
        return !limit || limit.period?.toLowerCase() === 'unlimited';
    }
    if ((noLimit(rateLimit)) && (noLimit(adminRateLimit) )) return false;

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

        const calcIsRateLimited = (limit, rateData, adminSet = false) => {
            //periods include Monthly, Daily, Hourly 
            const period = limit.period
            const colName = `${period.toLowerCase()}Cost`
            let spent = rateData[colName];
            if (period === 'Hourly') spent = spent[new Date().getHours()]// Get the current hour as a number from 0 to 23
            const isRateLimited = spent >= limit.rate;
            if (isRateLimited) {
                if (adminSet) params.body.options.rateLimit =  {...limit, adminSet};
                params.body.options.rateLimit.currentSpent = spent;
            }
            return isRateLimited;
        }
        return rateLimit ? calcIsRateLimited(rateLimit, rateData) : false || 
          adminRateLimit ? calcIsRateLimited(adminRateLimit, rateData, true) : false;
        
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
    if (limit.rate === undefined || limit.rate === null) return noRateLimit.period;
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
    return `$${limit.currentSpent} spent ${periodText}.`;
}