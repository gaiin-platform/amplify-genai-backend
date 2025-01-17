import os
import csv
import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize a DynamoDB client with Boto3
dynamodb = boto3.resource("dynamodb")

def load_model_rate_table():
    # Retrieve the environment variable for the table name
    table_name = os.environ["MODEL_RATE_TABLE"]

    # Access the DynamoDB table
    table = dynamodb.Table(table_name)

    # Define the correct path to the CSV file
    dir_path = os.path.dirname(os.path.realpath(__file__))
    csv_file_path = os.path.join(dir_path, "model_rate_values.csv")

    # Open the CSV file and read rows
    with open(csv_file_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                item = dict(row)

                # Convert specific columns to Decimal (or whatever type you need)
                item["InputCostPerThousandTokens"] = Decimal(row["InputCostPerThousandTokens"])
                item["OutputCostPerThousandTokens"] = Decimal(row["OutputCostPerThousandTokens"])

                response = table.put_item(Item=item)
            except ClientError as e:
                print(e.response["Error"]["Message"])
                return False

    # Return a success response after updating the table with all entries
    return True
