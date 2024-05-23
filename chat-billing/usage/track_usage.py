import os
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

# Initialize a DynamoDB resource
dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    time_range = context.function_name.rsplit("_", 1)[-1]
    # print("Time range:", time_range)
    if time_range in ("monthly", "daily"):
        track_usage(time_range)
    # for local testing
    elif time_range == "chat-billing-dev-trackUsage":
        track_usage("daily")
    else:
        error_message = f"Unexpected track usage trigger: {time_range}"
        print(error_message)
        return {"statusCode": 400, "body": json.dumps({"error": error_message})}

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Usage tracked successfully"}),
    }


# collect usage from tables and save cost to history table
def track_usage(time_range="daily"):
    # collect daily usage from chat usage table and additional charges table
    chat_usage_table_name = os.environ["CHAT_USAGE_TABLE"]
    additional_charges_table_name = os.environ["ADDITIONAL_CHARGES_TABLE"]

    chat_usage = query_usage_table(chat_usage_table_name, time_range=time_range)
    additional_charges = query_usage_table(
        additional_charges_table_name, time_range=time_range
    )

    # iterate through the items and calculate cost of each item
    for item in chat_usage:
        record_item_cost(item, time_range)

    # TODO: handle additional_charges
    # for item in additional_charges:
    #     record_additional_item_cost(item, time_range)

    return


# query usage tables based on the provided range
def query_usage_table(table_name, time_range="daily"):
    current_time = datetime.now(timezone.utc)

    if time_range == "daily":
        # Get the date string for the previous day
        date_string = (current_time - timedelta(days=1)).strftime("%Y-%m-%d")
    elif time_range == "monthly":
        # Get the date string for the beginning of the previous month
        first_day_previous_month = current_time.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        date_string = first_day_previous_month.strftime("%Y-%m")
    else:
        raise ValueError(f"Unsupported time_range value: {time_range}")

    # a single scan request can retrieve up to only 1 MB of data
    # to retrieve additional items beyond the 1 MB limit, you need to perform another scan operation using the LastEvaluatedKey value
    # as your ExclusiveStartKey in the next request to continue scanning from where the previous operation stopped
    table = dynamodb.Table(table_name)
    scan_kwargs = {"FilterExpression": Key("time").begins_with(date_string)}
    done = False
    start_key = None
    items = []

    while not done:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    # print("Table name:", table_name)
    # print("Date string:", date_string)
    # print("Items returned from table:", items)

    # Return the items from the response
    return items


def record_additional_item_cost():
    # TODO: handle code interpreter session

    # TODO: handle code interpreter usage (extract usage info out of details field)
    print()


def record_code_interpreter_session_item_cost(
    account_type,
    identifier,
    user,
    time_range,
):
    # Define the cost for a code interpreter session
    session_cost = Decimal("0.03")

    # Get the previous day's date and time
    current_time = datetime.now(timezone.utc)
    date = ""

    if time_range == "daily":
        # Set start and end time for the previous day
        start_time = current_time - timedelta(days=1)
        start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        date = start_time.strftime("%Y-%m-%d")
    elif time_range == "monthly":
        # Set start time to the beginning of the previous month
        first_day_current_month = current_time.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        start_time = first_day_current_month - timedelta(days=1)
        start_time = start_time.replace(day=1)
        date = start_time.strftime("%Y-%m")
    else:
        raise ValueError(f"Unsupported time_range value: {time_range}")

    # Access the History Usage Table
    history_usage_table_name = os.environ["HISTORY_USAGE_TABLE"]
    history_usage_table = dynamodb.Table(history_usage_table_name)

    # Get the current cost values for the given identifier
    user_date_composite = user + "#" + date
    try:
        response = history_usage_table.get_item(
            Key={"id": identifier, "userDateComposite": user_date_composite}
        )
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for identifier '{identifier}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    if time_range == "daily":
        updated_costs = {
            "dailyCost": Decimal(current_costs.get("dailyCost", 0)) + session_cost,
        }
    elif time_range == "monthly":
        updated_costs = {
            "monthlyCost": Decimal(current_costs.get("monthlyCost", 0)) + session_cost,
        }
    else:
        raise ValueError(f"Unsupported time_range value: {time_range}")

    # Update the History Usage Table with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for identifier '{identifier}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for identifier '{identifier}' with new values: {updated_costs}"
            )

        if time_range == "daily":
            history_usage_table.update_item(
                Key={"id": identifier, "userDateComposite": user_date_composite},
                UpdateExpression="SET dailyCost = :dc, accountType = :at, #dt = :dt, #us = :us",
                ExpressionAttributeValues={
                    ":dc": updated_costs["dailyCost"],
                    ":at": account_type,
                    ":dt": date,
                    ":us": user,
                },
                ExpressionAttributeNames={
                    "#dt": "date",  # Placeholder for reserved keyword date
                    "#us": "user",  # Placeholder for reserved keyword user
                },
            )
        elif time_range == "monthly":
            history_usage_table.update_item(
                Key={"id": identifier, "userDateComposite": user_date_composite},
                UpdateExpression="SET monthlyCost = :mc, accountType = :at, #dt = :dt, #us = :us",
                ExpressionAttributeValues={
                    ":mc": updated_costs["monthlyCost"],
                    ":at": account_type,
                    ":dt": date,
                    ":us": user,
                },
                ExpressionAttributeNames={
                    "#dt": "date",  # Placeholder for reserved keyword date
                    "#us": "user",  # Placeholder for reserved keyword user
                },
            )
        else:
            raise ValueError(f"Unsupported time_range value: {time_range}")
    except Exception as e:
        print(
            f"Error updating costs for identifier '{identifier}' and userDateComposite '{user_date_composite}': {e}"
        )


# extract attributes from item and tables, and pass to helper function
def record_item_cost(item, time_range):
    user = item.get("user", "")
    account_id = item.get("accountId", "")
    if (
        account_id == "general_account"
        or account_id == "270.05.27780.XXXX.200.000.000.0.0."
    ):
        account_type = "user"
        identifier = user
    else:
        account_type = "coa"
        identifier = account_id
    model_id = item.get("modelId", "")
    input_tokens = Decimal(item.get("inputTokens", 0))
    output_tokens = Decimal(item.get("outputTokens", 0))
    # print("input tokens:", input_tokens)
    # print("output tokens:", output_tokens)

    # Access the model exchange rate table table
    exchange_rate_table_name = os.environ["MODEL_EXCHANGE_RATE_TABLE"]
    exchange_rate_table = dynamodb.Table(exchange_rate_table_name)

    # Query the table for the matching ModelID
    response = exchange_rate_table.query(
        KeyConditionExpression=Key("ModelID").eq(model_id)
    )

    # Check if model_id matches a ModelID record in the table
    if response["Items"]:
        # Assuming there's only one match, extract the rates
        exchange_rate_record = response["Items"][0]
        input_cost_per_thousand_tokens = exchange_rate_record[
            "InputCostPerThousandTokens"
        ]
        output_cost_per_thousand_tokens = exchange_rate_record[
            "OutputCostPerThousandTokens"
        ]

        bill_chat_to_identifier(
            account_type=account_type,
            identifier=identifier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_per_thousand_tokens=input_cost_per_thousand_tokens,
            output_cost_per_thousand_tokens=output_cost_per_thousand_tokens,
            user=user,
            time_range=time_range,
        )
    else:
        print(f"No exchange rate found for ModelID: {model_id}")

    return


# calculate and record the cost of the item (whose attributes are provided) to the history table
def bill_chat_to_identifier(
    account_type,
    identifier,
    input_tokens,
    output_tokens,
    input_cost_per_thousand_tokens,
    output_cost_per_thousand_tokens,
    user,
    time_range,
):

    # Calculate the total cost for the chat
    input_cost_total = (input_tokens / 1000) * input_cost_per_thousand_tokens
    output_cost_total = (output_tokens / 1000) * output_cost_per_thousand_tokens
    total_cost = input_cost_total + output_cost_total
    # print("Total Cost:", total_cost)

    # Get the previous day's date and time
    current_time = datetime.now(timezone.utc)
    date = ""

    if time_range == "daily":
        # Set start and end time for the previous day
        start_time = current_time - timedelta(days=1)
        start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        date = start_time.strftime("%Y-%m-%d")
    elif time_range == "monthly":
        # Set start time to the beginning of the previous month
        first_day_current_month = current_time.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        start_time = first_day_current_month - timedelta(days=1)
        start_time = start_time.replace(day=1)
        date = start_time.strftime("%Y-%m")
    else:
        raise ValueError(f"Unsupported time_range value: {time_range}")

    # Access the History Usage Table
    history_usage_table_name = os.environ["HISTORY_USAGE_TABLE"]
    history_usage_table = dynamodb.Table(history_usage_table_name)

    # Get the current cost values for the given identifier
    user_date_composite = user + "#" + date
    try:
        response = history_usage_table.get_item(
            Key={"id": identifier, "userDateComposite": user_date_composite}
        )
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for identifier '{identifier}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    if time_range == "daily":
        updated_costs = {
            "dailyCost": Decimal(current_costs.get("dailyCost", 0)) + total_cost,
        }
    elif time_range == "monthly":
        updated_costs = {
            "monthlyCost": Decimal(current_costs.get("monthlyCost", 0)) + total_cost,
        }
    else:
        raise ValueError(f"Unsupported time_range value: {time_range}")

    # Update the History Usage Table with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for identifier '{identifier}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for identifier '{identifier}' with new values: {updated_costs}"
            )

        if time_range == "daily":
            history_usage_table.update_item(
                Key={"id": identifier, "userDateComposite": user_date_composite},
                UpdateExpression="SET dailyCost = :dc, accountType = :at, #dt = :dt, #us = :us",
                ExpressionAttributeValues={
                    ":dc": updated_costs["dailyCost"],
                    ":at": account_type,
                    ":dt": date,
                    ":us": user,
                },
                ExpressionAttributeNames={
                    "#dt": "date",  # Placeholder for reserved keyword date
                    "#us": "user",  # Placeholder for reserved keyword user
                },
            )
        elif time_range == "monthly":
            history_usage_table.update_item(
                Key={"id": identifier, "userDateComposite": user_date_composite},
                UpdateExpression="SET monthlyCost = :mc, accountType = :at, #dt = :dt, #us = :us",
                ExpressionAttributeValues={
                    ":mc": updated_costs["monthlyCost"],
                    ":at": account_type,
                    ":dt": date,
                    ":us": user,
                },
                ExpressionAttributeNames={
                    "#dt": "date",  # Placeholder for reserved keyword date
                    "#us": "user",  # Placeholder for reserved keyword user
                },
            )
        else:
            raise ValueError(f"Unsupported time_range value: {time_range}")

    except Exception as e:
        print(
            f"Error updating costs for identifier '{identifier}' and userDateComposite '{user_date_composite}': {e}"
        )
