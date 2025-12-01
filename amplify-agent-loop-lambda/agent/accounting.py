import os
import uuid
import boto3
from datetime import datetime, timezone


dynamodb = boto3.client("dynamodb")

# Environment variables
dynamoTableName = os.environ.get("CHAT_USAGE_DYNAMO_TABLE")
costDynamoTableName = os.environ.get("COST_CALCULATIONS_DYNAMO_TABLE")
modelRateDynamoTable = os.environ.get("MODEL_RATE_TABLE")


def record_usage(
    account,
    request_id,
    model_id,
    input_tokens,
    output_tokens,
    cached_tokens,
    details=None,
):
    """
    Records usage and costs in DynamoDB tables.
    """
    if not dynamoTableName:
        print(
            "CHAT_USAGE_DYNAMO_TABLE table is not provided in the environment variables."
        )
        return 0.0

    if not costDynamoTableName:
        print(
            "COST_CALCULATIONS_DYNAMO_TABLE table is not provided in the environment variables."
        )
        return 0.0

    if not modelRateDynamoTable:
        print("MODEL_RATE_TABLE table is not provided in the environment variables.")
        return 0.0
    
    api_key_id = get_api_key_id(account)

    try:
        account_id = account.get("account_id", "general_account")

        if details is None:
            details = {}

        if api_key_id:
            details = {**details, "api_key_id": api_key_id}

        item = {
            "id": {"S": str(uuid.uuid4())},
            "requestId": {"S": request_id},
            "user": {"S": account["user"]},
            "time": {"S": datetime.now().isoformat()},
            "accountId": {"S": account_id},
            "inputTokens": {"N": str(input_tokens)},
            "outputTokens": {"N": str(output_tokens)},
            "modelId": {"S": model_id},
            "details": {
                "M": boto3.dynamodb.types.TypeSerializer().serialize(details)["M"]
            },
        }

        dynamodb.put_item(TableName=dynamoTableName, Item=item)
        print(f"Usage recorded for user: {account['user']}")

    except Exception as e:
        print(f"Error recording usage: {e}")
        return 0.0

    try:
        model_rate_response = dynamodb.query(
            TableName=modelRateDynamoTable,
            KeyConditionExpression="ModelID = :modelId",
            ExpressionAttributeValues={":modelId": {"S": model_id}},
        )

        if (
            not model_rate_response.get("Items")
            or len(model_rate_response["Items"]) == 0
        ):
            print(f"No model rate found for ModelID: {model_id}")
            return 0.0

        model_rate = model_rate_response["Items"][0]
        input_cost_per_thousand_tokens = float(
            model_rate["InputCostPerThousandTokens"]["N"]
        )
        output_cost_per_thousand_tokens = float(
            model_rate["OutputCostPerThousandTokens"]["N"]
        )
        cached_cost_per_thousand_tokens = float(
            model_rate["CachedCostPerThousandTokens"]["N"]
        )

        input_cost = (input_tokens / 1000) * input_cost_per_thousand_tokens
        output_cost = (output_tokens / 1000) * output_cost_per_thousand_tokens
        cached_cost = (cached_tokens / 1000) * cached_cost_per_thousand_tokens
        total_cost = input_cost + output_cost + cached_cost

        print(f"-- Total cost -- {total_cost}")

        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Create the accountInfo (secondary key)
        coa_string = account.get("accountId", "general_account")
        apiKeyIdInfo = api_key_id or "NA"
        account_info = f"{coa_string}#{apiKeyIdInfo}"

        # First update: Ensure dailyCost and hourlyCost are initialized
        empty_list = [{"N": "0"} for _ in range(24)]

        dynamodb.update_item(
            TableName=costDynamoTableName,
            Key={"id": {"S": account["user"]}, "accountInfo": {"S": account_info}},
            UpdateExpression="SET dailyCost = if_not_exists(dailyCost, :zero), hourlyCost = if_not_exists(hourlyCost, :emptyList), record_type = if_not_exists(record_type, :recordType)",
            ExpressionAttributeValues={
                ":zero": {"N": "0"},
                ":emptyList": {"L": empty_list},
                ":recordType": {"S": "cost"},
            },
        )

        # Second update: Update dailyCost and the specific hourlyCost index
        dynamodb.update_item(
            TableName=costDynamoTableName,
            Key={"id": {"S": account["user"]}, "accountInfo": {"S": account_info}},
            UpdateExpression=f"SET dailyCost = dailyCost + :totalCost ADD hourlyCost[{current_hour}] :totalCost",
            ExpressionAttributeValues={":totalCost": {"N": str(total_cost)}},
        )
        print(f"Updated dailyCost and hourlyCost")
        return total_cost
    except Exception as e:
        print(f"Error calculating or updating cost: {e}")
    return 0.0


def get_api_key_id(account):
    """
    Returns the API key ID if the access token starts with "amp-" and apiKeyId exists.
    """
    if account.get("accessToken", "").startswith("amp-") and account.get("api_key_id"):
        return account["api_key_id"]
    return None
