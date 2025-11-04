import os
import csv
import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize a DynamoDB client with Boto3
dynamodb = boto3.resource("dynamodb")

from pycommon.logger import getLogger
logger = getLogger("models_csv_updates")

def load_model_rate_table(model_data=None):
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

                # Check if model already exists and update accordingly
                try:
                    existing_response = table.get_item(Key={"ModelID": model_id})
                    existing_item = existing_response.get("Item", {})
                except:
                    existing_item = {}
                
                if not existing_item:
                    # New model - full insert
                    logger.info(f"ModelID {model_id} is new, adding complete record")
                    table.put_item(Item=csv_item)
                else:
                    # Existing model - check if outdated and update only if needed
                    is_outdated = check_old_data_by_col(existing_item.keys())
                    if is_outdated:
                        # Add missing new columns only
                        missing_attributes = {}
                        for key, value in csv_item.items():
                            if key not in existing_item:
                                missing_attributes[key] = value
                        
                        if missing_attributes:
                            logger.info(f"ModelID {model_id} - adding missing columns: {list(missing_attributes.keys())}")
                            
                            update_expression_parts = []
                            expression_attribute_values = {}
                            
                            for attr, value in missing_attributes.items():
                                update_expression_parts.append(f"#{attr} = :{attr}")
                                expression_attribute_values[f":{attr}"] = value
                            
                            update_expression = "SET " + ", ".join(update_expression_parts)
                            expression_attribute_names = {f"#{attr}": attr for attr in missing_attributes.keys()}
                            
                            table.update_item(
                                Key={"ModelID": model_id},
                                UpdateExpression=update_expression,
                                ExpressionAttributeNames=expression_attribute_names,
                                ExpressionAttributeValues=expression_attribute_values
                            )
                    else:
                        logger.info(f"ModelID {model_id} is already up to date, no changes needed")

            except ClientError as e:
                logger.error(e.response["Error"]["Message"])
                return False

    return True


old_cols = [
    "ModelID",
    "InputCostPerThousandTokens", 
    "ModelName",
    "OutputCostPerThousandTokens",
    "Provider",
]

# Required columns that indicate a model has the new schema
required_new_cols = [
    "InputCachedCostPerThousandTokens",
    "OutputCachedCostPerThousandTokens"
]

def check_old_data_by_col(model_cols):
    # Consider a model "old" if it's missing the new cached cost columns
    if not model_cols:
        return True
    
    # Check if it has the new cached cost columns
    has_new_columns = all(col in model_cols for col in required_new_cols)
    return not has_new_columns


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
                "InputCachedCostPerThousandTokens",
                "OutputCachedCostPerThousandTokens",
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
