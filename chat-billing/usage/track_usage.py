# This file should contain the implementation for the handler function to track usage declared in chat-billing/serverless.yml
# the hanlder should be triggered every time there is a new entry into the 'chat-billing-dev-billing' table
# check what the string within the "itemType" field in the table is, and call a different function depending on the value
# itemType could be chat, lambda, assistant, etc.

import os
import json
import boto3
from boto3.dynamodb.types import TypeDeserializer
from boto3.dynamodb.conditions import Key

# Initialize DynamoDB client
dynamodb_client = boto3.client("dynamodb")
# Initialize DynamoDB resource
dynamodb = boto3.resource("dynamodb")


# Helper function to deserialize DynamoDB stream image to Python dictionary
def deserialize_dynamodb_stream_image(stream_image):
    deserializer = TypeDeserializer()
    return {k: deserializer.deserialize(v) for k, v in stream_image.items()}


def handle_chat_item(item):
    # print("Handling chat item:", item)

    # Extract ModelID from the item
    model_id = item["modelId"]

    # Access the model exchange rate table table
    exchange_rate_table_name = os.environ["MODEL_EXCHANGE_RATE_TABLE"]
    exchange_rate_table = dynamodb.Table(exchange_rate_table_name)

    # Query the table for the matching ModelID
    response = exchange_rate_table.query(
        KeyConditionExpression=Key("ModelID").eq(model_id)
    )

    # Check if we got a matching exchange rate record
    if response["Items"]:
        # Assuming there's only one match, extract the rates
        exchange_rate_record = response["Items"][0]
        input_cost_per_thousand_tokens = exchange_rate_record[
            "InputCostPerThousandTokens"
        ]
        output_cost_per_thousand_tokens = exchange_rate_record[
            "OutputCostPerThousandTokens"
        ]

        bill_chat_to_coa(
            coa_string=item["accountId"],
            inputTokens=item["inputTokens"],
            outputTokens=item["outputTokens"],
            input_cost_per_thousand_tokens=input_cost_per_thousand_tokens,
            output_cost_per_thousand_tokens=output_cost_per_thousand_tokens,
        )
    else:
        print(f"No exchange rate found for ModelID: {model_id}")


def calculate_cost(input_tokens, output_tokens, input_cost, output_cost):
    # Assuming costs are per thousand tokens, we divide by 1000
    input_cost_total = (input_tokens / 1000) * input_cost
    output_cost_total = (output_tokens / 1000) * output_cost
    total_cost = input_cost_total + output_cost_total
    # print("Total Cost:", total_cost)
    return total_cost


def bill_chat_to_coa(
    coa_string,
    inputTokens,
    outputTokens,
    input_cost_per_thousand_tokens,
    output_cost_per_thousand_tokens,
):

    # Calculate the total cost for the chat
    total_cost = calculate_cost(
        inputTokens,
        outputTokens,
        input_cost_per_thousand_tokens,
        output_cost_per_thousand_tokens,
    )
    # print("Total Cost:", total_cost)

    # Access the UsagePerCoaTable
    usage_per_coa_table_name = os.environ["USAGE_PER_COA_TABLE"]
    usage_per_coa_table = dynamodb.Table(usage_per_coa_table_name)

    # Try to get the current cost values for the given COA
    try:
        response = usage_per_coa_table.get_item(Key={"coa": coa_string})
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for COA '{coa_string}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    updated_costs = {
        "dailyCost": current_costs.get("dailyCost", 0) + total_cost,
        "monthlyCost": current_costs.get("monthlyCost", 0) + total_cost,
        "totalCost": current_costs.get("totalCost", 0) + total_cost,
    }

    # Update the UsagePerCoaTable with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for COA '{coa_string}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for COA '{coa_string}' with new values: {updated_costs}"
            )

        usage_per_coa_table.update_item(
            Key={"coa": coa_string},
            UpdateExpression="SET dailyCost = :dc, monthlyCost = :mc, totalCost = :tc",
            ExpressionAttributeValues={
                ":dc": updated_costs["dailyCost"],
                ":mc": updated_costs["monthlyCost"],
                ":tc": updated_costs["totalCost"]
            },
        )
    except Exception as e:
        print(f"Error updating costs for COA '{coa_string}': {e}")


def handle_code_interpreter_item(item):
    print("Charging For Code Interpreter")
    handle_chat_item(item)


def handle_code_interpreter_session_item(item):
    print("Charging For Code Interpreter Session")

    # Define the cost for a code interpreter session
    session_cost = 0.03

    # Extract the COA (Chart of Accounts) string from the item
    coa_string = item["accountId"]

    # Access the UsagePerCoaTable
    usage_per_coa_table_name = os.environ["USAGE_PER_COA_TABLE"]
    usage_per_coa_table = dynamodb.Table(usage_per_coa_table_name)

    # Try to get the current cost values for the given COA
    try:
        response = usage_per_coa_table.get_item(Key={"coa": coa_string})
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for COA '{coa_string}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    updated_costs = {
        "dailyCost": current_costs.get("dailyCost", 0) + session_cost,
        "monthlyCost": current_costs.get("monthlyCost", 0) + session_cost,
        "totalCost": current_costs.get("totalCost", 0) + session_cost,
    }

    # Update the UsagePerCoaTable with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for COA '{coa_string}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for COA '{coa_string}' with new values: {updated_costs}"
            )

        usage_per_coa_table.update_item(
            Key={"coa": coa_string},
            UpdateExpression="SET dailyCost = :dc, monthlyCost = :mc, totalCost = :tc",
            ExpressionAttributeValues={
                ":dc": updated_costs["dailyCost"],
                ":mc": updated_costs["monthlyCost"],
                ":tc": updated_costs["totalCost"]
            },
        )
    except Exception as e:
        print(f"Error updating costs for COA '{coa_string}': {e}")


def handle_other_item_types(item):
    itemType = item["itemType"]
    print("unknown itemType:", itemType)


def handler(event, context):
    try:
        for record in event["Records"]:
            if record["eventName"] == "INSERT":
                new_image = deserialize_dynamodb_stream_image(
                    record["dynamodb"]["NewImage"]
                )

                coa_string = new_image.get("accountId", "UnknownAccountId")
                item_type = new_image.get("itemType", "UnknownItemType")
                # print(f"COA String: {coa_string}, Item Type: {item_type}")

                if (
                    coa_string != "general_account"
                    and coa_string != "270.05.27780.XXXX.200.000.000.0.0."
                ):
                    if item_type == "chat":
                        handle_chat_item(new_image)
                    elif item_type == "codeInterpreter":
                        handle_code_interpreter_item(new_image)
                    elif item_type == "codeInterpreterSession":
                        handle_code_interpreter_session_item(new_image)
                    else:
                        handle_other_item_types(new_image)
    except Exception as e:
        print(f"An error occurred in the handler: {e}")
        raise

    return {"statusCode": 200, "body": "Usage tracked successfully"}
