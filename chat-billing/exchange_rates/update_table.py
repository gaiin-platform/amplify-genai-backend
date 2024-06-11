
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

# to run this function and update the table, after deploying this lambda, run:
# ~ serverless invoke --function updateModelExchangeRateTable --stage dev --log

import os
import csv
import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize a DynamoDB client with Boto3
dynamodb = boto3.resource("dynamodb")


def updateModelExchangeRateTable(event, context):
    # Retrieve the environment variable for the table name
    table_name = os.environ["MODEL_EXCHANGE_RATE_TABLE"]

    # Access the DynamoDB table
    table = dynamodb.Table(table_name)

    # Define the correct path to the CSV file
    dir_path = os.path.dirname(os.path.realpath(__file__))
    csv_file_path = os.path.join(dir_path, "exchange_rate_values.csv")

    # Open the CSV file and read rows
    with open(csv_file_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                # Convert to Decimal instead of float
                input_cost = Decimal(row["InputCostPerThousandTokens"])
                output_cost = (
                    Decimal(row["OutputCostPerThousandTokens"])
                    if row["OutputCostPerThousandTokens"]
                    else None
                )

                # Each row in the CSV file corresponds to an item in the table
                item = {
                    "ModelID": row["ModelID"],
                    "ModelName": row["ModelName"],
                    "InputCostPerThousandTokens": input_cost,
                }

                # Only add OutputCostPerThousandTokens if it's present
                if output_cost is not None:
                    item["OutputCostPerThousandTokens"] = output_cost

                response = table.put_item(Item=item)
            except ClientError as e:
                print(e.response["Error"]["Message"])
                return {
                    "statusCode": 500,
                    "body": json.dumps("Error updating exchange rate table."),
                }

    # Return a success response after updating the table with all entries
    return {
        "statusCode": 200,
        "body": json.dumps("Exchange rate table updated successfully."),
    }
