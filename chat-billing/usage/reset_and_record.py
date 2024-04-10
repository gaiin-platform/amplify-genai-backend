# Needs to implement a function triggered by an event bridge event at the beginning of each day and beginning of each month
# The trigger will need to write the coa, date and daily_usage or monthly_usage (from the UsagePerCoaTable) to the history-coa-usage table and set the dailyCost and monthlyCost values (within the UsagePerCoaTable) to zero

import os
import boto3
from datetime import datetime, timezone

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    # Extract the type of reset from the event input
    reset_type = event.get("type")

    # Determine the table names from the environment variables
    history_table_name = os.environ.get("HISTORY_COA_USAGE_TABLE")
    usage_table_name = os.environ.get("USAGE_PER_COA_TABLE")

    # Get the table resources
    history_table = dynamodb.Table(history_table_name)
    usage_table = dynamodb.Table(usage_table_name)

    # Get the current UTC time
    now = datetime.now(timezone.utc)

    # Scan the usage table to get all COA items
    response = usage_table.scan()
    items = response.get("Items", [])

    # Process each item from the usage table
    for item in items:
        coa = item["coa"]
        daily_cost = item.get("dailyCost", 0)
        monthly_cost = item.get("monthlyCost", 0)

        # Create the history item based on the reset type
        if reset_type == "dailyReset":
            history_item = {
                "date": now.strftime("%Y-%m-%d"),
                "coa": coa,
                "dailyCost": daily_cost,
            }
            # Reset the daily usage in the usage table
            usage_table.update_item(
                Key={"coa": coa},
                UpdateExpression="SET dailyCost = :val",
                ExpressionAttributeValues={":val": 0},
            )

        elif reset_type == "monthlyReset":
            history_item = {
                "date": now.strftime("%Y-%m-01"),
                "coa": coa,
                "monthlyCost": monthly_cost,
            }
            # Reset the monthly usage in the usage table
            usage_table.update_item(
                Key={"coa": coa},
                UpdateExpression="SET monthlyCost = :val",
                ExpressionAttributeValues={":val": 0},
            )

        # Add the history item to the history table
        history_table.put_item(Item=history_item)

    return {"statusCode": 200, "body": f"Successfully processed {reset_type}"}
