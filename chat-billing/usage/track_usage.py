
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json

# Initialize a DynamoDB resource
dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    try:
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
    
    except KeyError as e:
        error_message = f"Missing key error: {str(e)}"
        print(error_message)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_message}),
        }

    except Exception as e:
        error_message = f"An unknown error occurred: {str(e)}"
        print(error_message)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_message}),
        }


# collect usage from tables and save cost to history table
def track_usage(time_range="daily"):
    try:
        # collect daily usage from chat usage table and additional charges table
        chat_usage_table_name = os.environ["CHAT_USAGE_TABLE"]
        additional_charges_table_name = os.environ["ADDITIONAL_CHARGES_TABLE"]
        if not chat_usage_table_name or not additional_charges_table_name:
            raise KeyError("CHAT_USAGE_TABLE and ADDITIONAL_CHARGES_TABLE must be set.")

        chat_usage_items = query_usage_table(chat_usage_table_name, time_range=time_range)
        additional_charges_items = query_usage_table(
            additional_charges_table_name, time_range=time_range
        )

        # iterate through the items and calculate cost of each item
        for item in chat_usage_items:
            record_item_cost(item, time_range)

        for item in additional_charges_items:
            record_additional_item_cost(item, time_range)

    except KeyError as e:
        error_message = f"Missing environment variable: {str(e)}"
        print(error_message)
        raise

    except Exception as e:
        error_message = f"An unknown error occurred in track_usage: {str(e)}"
        print(error_message)
        raise

    return


# query usage tables based on the provided range
def query_usage_table(table_name, time_range="daily"):
    try:
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
    
    except ValueError as e:
        error_message = f"Value error: {str(e)}"
        print(error_message)
        raise

    except (BotoCoreError, ClientError) as e:
        error_message = f"DynamoDB error: {str(e)}"
        print(error_message)
        raise

    except Exception as e:
        error_message = f"An unknown error occurred: {str(e)}"
        print(error_message)
        raise


def record_additional_item_cost(item, time_range):
    try:
        itemType = item.get("itemType", "")

        if itemType == "codeInterpreter":
            record_code_interpreter_item_cost(item, time_range)
        elif itemType == "codeInterpreterSession":
            bill_cost_to_identifier(
                item, time_range, 0.03
            )  # cost of code interpreter session is $0.03
        else:
            raise ValueError(f"Unsupported itemType value: {itemType}")
    
    except ValueError as e:
        error_message = f"Value error: {str(e)}"
        print(error_message)
        raise
    
    except KeyError as e:
        error_message = f"Missing key error: {str(e)}"
        print(error_message)
        raise
    
    except Exception as e:
        error_message = f"An unknown error occurred: {str(e)}"
        print(error_message)
        raise

    return


def record_code_interpreter_item_cost(item, time_range):
    cost = 0
    # parse details field to calculate input and output tokens
    return
    bill_cost_to_identifier(item, time_range, cost)


# Extract attributes from item and tables, and pass to helper function
def record_item_cost(item, time_range):
    try:
        model_id = item.get("modelId", "")
        input_tokens = Decimal(item.get("inputTokens", 0))
        output_tokens = Decimal(item.get("outputTokens", 0))
        # print("input tokens:", input_tokens)
        # print("output tokens:", output_tokens)

        # Access the model rate table table
        model_rate_table_name = os.environ["MODEL_RATE_TABLE"]
        model_rate_table = dynamodb.Table(model_rate_table_name)

        # Query the table for the matching ModelID
        response = model_rate_table.query(
            KeyConditionExpression=Key("ModelID").eq(model_id)
        )

        # Check if model_id matches a ModelID record in the table
        if response["Items"]:
            # Assuming there's only one match, extract the rates
            model_rate_record = response["Items"][0]
            input_cost_per_thousand_tokens = model_rate_record[
                "InputCostPerThousandTokens"
            ]
            output_cost_per_thousand_tokens = model_rate_record[
                "OutputCostPerThousandTokens"
            ]

            # Calculate the total cost for the chat
            input_cost_total = (input_tokens / 1000) * input_cost_per_thousand_tokens
            output_cost_total = (output_tokens / 1000) * output_cost_per_thousand_tokens
            total_cost = input_cost_total + output_cost_total
            # print("Total Cost:", total_cost)

            bill_cost_to_identifier(
                item=item,
                time_range=time_range,
                cost=total_cost,
            )
        else:
            print(f"No model rate found for ModelID: {model_id}")

    except KeyError as e:
        error_message = f"Missing key error: {str(e)}"
        print(error_message)
        raise

    except InvalidOperation as e:
        error_message = f"Decimal conversion error: {str(e)}"
        print(error_message)
        raise

    except (BotoCoreError, ClientError) as e:
        error_message = f"DynamoDB error: {str(e)}"
        print(error_message)
        raise

    except Exception as e:
        error_message = f"An unknown error occurred: {str(e)}"
        print(error_message)
        raise

    return


# add cost to a user and coa's record in the history table
def bill_cost_to_identifier(
    item,
    time_range,
    cost,
):
    current_time = None
    date = None
    response = None
    identifier = None
    account_type = None
    current_costs = {}
    updated_costs = {}

    # extract relevant user info out of item parameter
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

    # Get the previous day's date and time
    try:
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
    except Exception as e:
        print(
            f"Error fetching previous day/month date and time for time range {time_range}: {e}"
        )

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

    # Prepare the updated cost values
    try:
        if time_range == "daily":
            updated_costs = {
                "dailyCost": Decimal(current_costs.get("dailyCost", 0)) + cost,
            }
        elif time_range == "monthly":
            updated_costs = {
                "monthlyCost": Decimal(current_costs.get("monthlyCost", 0)) + cost,
            }
        else:
            raise ValueError(f"Unsupported time_range value: {time_range}")
    except Exception as e:
        print(f"Error adding {cost} to existing cost: {e}")

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
