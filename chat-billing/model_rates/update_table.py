# to run this function and update the table, after deploying this lambda, run:
# ~ serverless invoke --function updateModelRateTable --stage dev --log

import os
import csv
import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize a DynamoDB client with Boto3
dynamodb = boto3.resource("dynamodb")


def updateModelRateTable(event, context):
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
                    "Provider": row["Provider"],
                }

                # Only add OutputCostPerThousandTokens if it's present
                if output_cost is not None:
                    item["OutputCostPerThousandTokens"] = output_cost

                response = table.put_item(Item=item)
            except ClientError as e:
                print(e.response["Error"]["Message"])
                return {
                    "statusCode": 500,
                    "body": json.dumps("Error updating model rate table."),
                }

    # Return a success response after updating the table with all entries
    return {
        "statusCode": 200,
        "body": json.dumps("Model rate table updated successfully."),
    }


# TODO: check to make sure this works when deployed (possible errors pulling in AWS credentials)
def get_mistral_bedrock_pricing():
    try:
        client = boto3.client("pricing", region_name="us-east-1")

        providers = ["Mistral"]
        pricing_data = {}

        for provider in providers:
            response = client.get_products(
                ServiceCode="AmazonBedrock",
                Filters=[
                    {
                        "Type": "TERM_MATCH",
                        "Field": "location",
                        "Value": "US East (N. Virginia)",
                    },
                    {
                        "Type": "TERM_MATCH",
                        "Field": "feature",
                        "Value": "On-demand Inference",
                    },
                    {"Type": "TERM_MATCH", "Field": "provider", "Value": provider},
                ],
            )
            # print(response, "\n")

            for price_item in response["PriceList"]:
                item = json.loads(price_item)
                model = item["product"]["attributes"]["model"]
                inference_type = item["product"]["attributes"]["inferenceType"]
                price_dimensions = item["terms"]["OnDemand"][
                    next(iter(item["terms"]["OnDemand"]))
                ]["priceDimensions"]
                cost_per_thousand = float(
                    next(iter(price_dimensions.values()))["pricePerUnit"]["USD"]
                )

                if model not in pricing_data:
                    pricing_data[model] = {
                        "ModelID": model,
                        "ModelName": model,  # Can add better name mapping here if required
                        "InputCostPerThousandTokens": 0.0,
                        "OutputCostPerThousandTokens": 0.0,
                    }

                if inference_type == "Input tokens":
                    pricing_data[model][
                        "InputCostPerThousandTokens"
                    ] = cost_per_thousand
                elif inference_type == "Output tokens":
                    pricing_data[model][
                        "OutputCostPerThousandTokens"
                    ] = cost_per_thousand

        # Print the formatted output
        print(
            "ModelID,ModelName,InputCostPerThousandTokens,OutputCostPerThousandTokens"
        )
        for model, data in pricing_data.items():
            print(
                f"{data['ModelID']},{data['ModelName']},{data['InputCostPerThousandTokens']},{data['OutputCostPerThousandTokens']}"
            )

    except Exception as e:
        print(f"Error fetching pricing data: {str(e)}")


# Call the function to get the pricing and print it
# get_mistral_bedrock_pricing()
