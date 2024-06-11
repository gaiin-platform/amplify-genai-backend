
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
from boto3.dynamodb.conditions import Key
from decimal import Decimal


def handle_chat_item(dynamodb, item, account_type, identifier, user):
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

        bill_chat_to_identifier(
            dynamodb=dynamodb,
            account_type=account_type,
            identifier=identifier,
            inputTokens=item["inputTokens"],
            outputTokens=item["outputTokens"],
            input_cost_per_thousand_tokens=input_cost_per_thousand_tokens,
            output_cost_per_thousand_tokens=output_cost_per_thousand_tokens,
            user=user,
        )
    else:
        print(f"No exchange rate found for ModelID: {model_id}")


def calculate_cost(input_tokens, output_tokens, input_cost, output_cost):
    # Assuming costs are per thousand tokens, we divide by 1000
    input_cost_total = (Decimal(input_tokens) / 1000) * Decimal(input_cost)
    output_cost_total = (Decimal(output_tokens) / 1000) * Decimal(output_cost)
    total_cost = input_cost_total + output_cost_total
    # print("Total Cost:", total_cost)
    return total_cost


def bill_chat_to_identifier(
    dynamodb,
    account_type,
    identifier,
    inputTokens,
    outputTokens,
    input_cost_per_thousand_tokens,
    output_cost_per_thousand_tokens,
    user,
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
    usage_per_coa_table_name = os.environ["USAGE_PER_ID_TABLE"]
    usage_per_coa_table = dynamodb.Table(usage_per_coa_table_name)

    # Try to get the current cost values for the given identifier
    try:
        response = usage_per_coa_table.get_item(Key={"id": identifier, "user": user})
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for identifier '{identifier}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    updated_costs = {
        "dailyCost": Decimal(current_costs.get("dailyCost", 0)) + total_cost,
        "monthlyCost": Decimal(current_costs.get("monthlyCost", 0)) + total_cost,
        "totalCost": Decimal(current_costs.get("totalCost", 0)) + total_cost,
    }

    # Update the UsagePerCoaTable with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for identifier '{identifier}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for identifier '{identifier}' with new values: {updated_costs}"
            )

        usage_per_coa_table.update_item(
            Key={"id": identifier, "user": user},
            UpdateExpression="SET dailyCost = :dc, monthlyCost = :mc, totalCost = :tc, accountType = :at",
            ExpressionAttributeValues={
                ":dc": updated_costs["dailyCost"],
                ":mc": updated_costs["monthlyCost"],
                ":tc": updated_costs["totalCost"],
                ":at": account_type,
            },
        )
    except Exception as e:
        print(f"Error updating costs for identifier '{identifier}': {e}")


def handle_code_interpreter_item(dynamodb, item, account_type, identifier, user):
    print("Charging For Code Interpreter")
    print(item)

    # Check if "inputTokens" or "outputTokens" exist in item
    if "inputTokens" in item or "outputTokens" in item:
        handle_chat_item(dynamodb, item, account_type, identifier, user)
    else:
        print("No input or output tokens attached to code interpreter request")


def handle_code_interpreter_session_item(
    dynamodb, item, account_type, identifier, user
):
    print("Charging For Code Interpreter Session")

    # Define the cost for a code interpreter session
    session_cost = Decimal("0.03")

    # Access the UsagePerCoaTable
    usage_per_coa_table_name = os.environ["USAGE_PER_ID_TABLE"]
    usage_per_coa_table = dynamodb.Table(usage_per_coa_table_name)

    # Try to get the current cost values for the given identifier
    try:
        response = usage_per_coa_table.get_item(Key={"id": identifier, "user": user})
        current_costs = response.get("Item", {})
    except Exception as e:
        print(f"Error fetching current costs for identifier '{identifier}': {e}")
        current_costs = {}

    # Prepare the updated cost values
    updated_costs = {
        "dailyCost": Decimal(current_costs.get("dailyCost", 0)) + session_cost,
        "monthlyCost": Decimal(current_costs.get("monthlyCost", 0)) + session_cost,
        "totalCost": Decimal(current_costs.get("totalCost", 0)) + session_cost,
    }

    # Update the UsagePerCoaTable with the new costs
    try:
        if "Item" not in response:
            print(
                f"Creating new cost entry for identifier '{identifier}' with values: {updated_costs}"
            )
        else:
            print(
                f"Updating existing cost entry for identifier '{identifier}' with new values: {updated_costs}"
            )

        usage_per_coa_table.update_item(
            Key={"id": identifier, "user": user},
            UpdateExpression="SET dailyCost = :dc, monthlyCost = :mc, totalCost = :tc, accountType = :at",
            ExpressionAttributeValues={
                ":dc": updated_costs["dailyCost"],
                ":mc": updated_costs["monthlyCost"],
                ":tc": updated_costs["totalCost"],
                ":at": account_type,
            },
        )
    except Exception as e:
        print(f"Error updating costs for identifier '{identifier}': {e}")


def handle_other_item_types(dynamodb, item, account_type, identifier, user):
    itemType = item["itemType"]
    print("unknown itemType:", itemType)
