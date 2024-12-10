import os
import boto3
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime, timezone
import csv
import io
from decimal import Decimal
from common.validate import validated
import json

# Initialize the DynamoDB client
dynamodb = boto3.resource("dynamodb")
history_usage_table_name = os.environ["HISTORY_USAGE_TABLE"]
chat_usage_table_name = os.environ["CHAT_USAGE_TABLE"]


# Helper function to create CSV from a list of dictionaries
def generate_csv(data_list):
    if not data_list:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data_list[0].keys())
    writer.writeheader()
    for data in data_list:
        writer.writerow(data)
    return output.getvalue()


# Function to calculate the cost per item
def calculate_cost(item, model_rate_table):
    model_id = item.get("modelId", "")
    input_tokens = Decimal(item.get("inputTokens", 0))
    output_tokens = Decimal(item.get("outputTokens", 0))

    # Query the model rate table for the cost per thousand tokens
    response = model_rate_table.query(
        KeyConditionExpression=Key("ModelID").eq(model_id)
    )

    # If no rate is found, return None to indicate the cost couldn't be calculated
    if not response["Items"]:
        print(f"No model rate found for ModelID: {model_id}")
        return None

    model_rate_record = response["Items"][0]
    input_cost_per_thousand_tokens = Decimal(
        model_rate_record["InputCostPerThousandTokens"]
    )
    output_cost_per_thousand_tokens = Decimal(
        model_rate_record["OutputCostPerThousandTokens"]
    )

    # Calculate total cost
    input_cost_total = (input_tokens / 1000) * input_cost_per_thousand_tokens
    output_cost_total = (output_tokens / 1000) * output_cost_per_thousand_tokens
    total_cost = input_cost_total + output_cost_total

    return total_cost


def calculate_and_record_todays_usage_costs(chat_records, model_rate_table):
    today_costs = []
    for item in chat_records:
        user = item.get("user", "")
        account_id = item.get("accountId", "")
        # Determine account type and identifier like in 'record_item_cost'
        if account_id in ("general_account", "270.05.27780.XXXX.200.000.000.0.0."):
            account_type = "user"
            identifier = user
        else:
            account_type = "coa"
            identifier = account_id

        # Reuse the logic from 'calculate_cost' to get total_cost
        total_cost = calculate_cost(item, model_rate_table)

        if total_cost is not None:
            # Creating the record in the same format as 'historyUsage'
            record = {
                "user": user,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "accountType": account_type,
                "dailyCost": "{:.8f}".format(
                    total_cost
                ),  # Ensuring the format matches 'historyUsage'
                "id": identifier,
                "userDateComposite": f"{user}#{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            }
            today_costs.append(record)

    # Aggregate costs by identifier for today's costs
    aggregated_costs = {}
    for record in today_costs:
        identifier = record["id"]
        if identifier not in aggregated_costs:
            aggregated_costs[identifier] = record
        else:
            # Aggregate the cost under the same identifier
            aggregated_costs[identifier]["dailyCost"] = "{:.8f}".format(
                Decimal(aggregated_costs[identifier]["dailyCost"])
                + Decimal(record["dailyCost"])
            )

    # Convert the aggregated records to a list
    return list(aggregated_costs.values())


@validated(op="report_generator")
def report_generator(event, context, current_user, name, data):
    try:
        body = json.loads(event["body"])
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON format"})}

    emails = body.get("emails")

    print("Emails:", emails)
    
    if not isinstance(emails, list):
        emails = [emails]

    history_table = dynamodb.Table(history_usage_table_name)
    history_records = []
    for email in emails:
        resp = history_table.scan(
            FilterExpression=Key("userDateComposite").begins_with(email)
            & Attr("user").eq(email)
        )
        history_records.extend(resp.get("Items", []))

    history_csv_data = generate_csv(history_records)

    chat_table = dynamodb.Table(chat_usage_table_name)
    current_time = datetime.now(timezone.utc)
    today_date_string = current_time.strftime("%Y-%m-%d")
    chat_records = []

    for email in emails:
        scan_kwargs = {
            "FilterExpression": Key("time").begins_with(today_date_string)
            & Attr("user").eq(email)
        }
        done = False
        start_key = None

        while not done:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key
            response = chat_table.scan(**scan_kwargs)
            chat_records.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey", None)
            done = start_key is None

    chat_csv_data = generate_csv(chat_records)

    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    today_costs_formatted = calculate_and_record_todays_usage_costs(
        chat_records, model_rate_table
    )

    chat_usage_today_costs_csv = generate_csv(today_costs_formatted)

    # Combine 'history_csv_data' and 'chat_usage_today_costs_csv' into 'allUsage'
    # Ensure to exclude the header row from 'chat_usage_today_costs_csv' when concatenating
    all_usage = history_csv_data
    if chat_usage_today_costs_csv:  # Check if there's any data for today's costs
        chat_usage_today_costs_csv_no_header = "\n".join(
            chat_usage_today_costs_csv.split("\n")[1:]
        )
        all_usage += chat_usage_today_costs_csv_no_header

    # return {
    #     "historyUsage": history_csv_data,
    #     "todayRequests": chat_csv_data,
    #     "todayUsage": chat_usage_today_costs_csv,
    #     "allUsage": all_usage,
    # }

    return all_usage
