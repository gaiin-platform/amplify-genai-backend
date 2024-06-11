
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

# Needs to implement a function triggered by an event bridge event at the beginning of each day and beginning of each month
# The trigger will need to write the coa, date and daily_usage or monthly_usage (from the UsagePerCoaTable) to the history-coa-usage table and set the dailyCost and monthlyCost values (within the UsagePerCoaTable) to zero

import os
import boto3
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    # Extract the type of reset from the event input
    reset_type = event.get("type")

    # Determine the table names from the environment variables
    history_table_name = os.environ.get("HISTORY_USAGE_TABLE")
    usage_table_name = os.environ.get("USAGE_PER_ID_TABLE")

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
        # Initialize history_item to None
        history_item = None

        id = item["id"]
        account_type = item["accountType"]
        daily_cost = item.get("dailyCost", 0)
        monthly_cost = item.get("monthlyCost", 0)
        user = item.get("user")

        # Create the history item based on the reset type
        if reset_type == "dailyReset" and daily_cost != 0:
            # Subtract a day to get the date of the day before
            date_before = now - relativedelta(days=1)
            user_date_composite = f"{user}#{date_before.strftime('%Y-%m-%d')}"
            history_item = {
                "id": id,
                "userDateComposite": user_date_composite,
                "date": date_before.strftime("%Y-%m-%d"),
                "user": user,
                "accountType": account_type,
                "dailyCost": daily_cost,
            }
            # Reset the daily usage in the usage table
            usage_table.update_item(
                Key={"id": id, "user": user},
                UpdateExpression="SET dailyCost = :val",
                ExpressionAttributeValues={":val": 0},
            )

        elif reset_type == "monthlyReset" and monthly_cost != 0:
            # Subtract a month to get the previous month
            month_before = now - relativedelta(months=1)
            user_date_composite = f"{user}#{month_before.strftime('%Y-%m-01')}"
            history_item = {
                "id": id,
                "userDateComposite": user_date_composite,
                "date": month_before.strftime("%Y-%m-01"),
                "user": user,
                "accountType": account_type,
                "monthlyCost": monthly_cost,
            }
            # Reset the monthly usage in the usage table
            usage_table.update_item(
                Key={"id": id, "user": user},
                UpdateExpression="SET monthlyCost = :val",
                ExpressionAttributeValues={":val": 0},
            )

        # Only add the history item to the history table if it has been defined
        if history_item is not None:
            history_table.put_item(Item=history_item)

    return {"statusCode": 200, "body": f"Successfully processed {reset_type}"}
