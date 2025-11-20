import os
import csv
import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize a DynamoDB client with Boto3
dynamodb = boto3.resource("dynamodb")


def load_model_rate_table(model_data):
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
                csv_item = parse_csv_row(row)
                model_id = csv_item["ModelID"]

                existing_item = model_data.get(model_id, {})
                if check_old_data_by_col(existing_item.keys()):
                    print(f"ModelID {model_id} is outdated/missing, adding new row")
                    response = table.put_item(Item=csv_item)
                else:
                    print(
                        f"ModelID {model_id} is up to date, updating existing col if missing"
                    )
                    updated_item = dict(existing_item)
                    for key, value in csv_item.items():
                        if key not in existing_item:
                            print("Updating column: ", key)
                            # Only add the column if it doesn't exist in the existing item
                            updated_item[key] = value

                    table.put_item(Item=updated_item)

            except ClientError as e:
                print(e.response["Error"]["Message"])
                return False

    # Return a success response after updating the table with all entries
    return True


old_cols = [
    "ModelID",
    "InputCostPerThousandTokens",
    "ModelName",
    "OutputCostPerThousandTokens",
    "Provider",
]


def check_old_data_by_col(model_cols):
    return not model_cols or sorted(model_cols) == sorted(old_cols)


def parse_csv_row(row_dict):
    """
    Converts the raw CSV row (string-based) into a typed dict
    (Decimal for numbers, bool for 'TRUE'/'FALSE', etc.)
    """
    item = {}
    for k, v in row_dict.items():
        if v is None:
            item[k] = None
            continue

        v_str = v.strip()

        # Convert "TRUE"/"FALSE" to booleans
        if v_str.upper() == "TRUE":
            item[k] = True
        elif v_str.upper() == "FALSE":
            item[k] = False
        else:
            # Convert known numeric columns to Decimal
            if k in {
                "InputCostPerThousandTokens",
                "OutputCostPerThousandTokens",
                "CachedCostPerThousandTokens",
                "InputContextWindow",
                "OutputTokenLimit",
            }:
                item[k] = Decimal(v_str)
            else:
                item[k] = v_str
    item["ExclusiveGroupAvailability"] = []

    return item


def get_csv_model_ids():
    """
    Opens the CSV file (model_rate_values.csv) and returns a set of model IDs
    found in the CSV.
    """
    import os
    import csv

    # Define the path to the CSV file relative to this file
    dir_path = os.path.dirname(os.path.realpath(__file__))
    csv_file_path = os.path.join(dir_path, "model_rate_values.csv")

    model_ids = set()
    with open(csv_file_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            model_id = row.get("ModelID")
            if model_id:
                model_ids.add(model_id.strip())
    return model_ids
