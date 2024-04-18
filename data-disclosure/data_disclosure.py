import boto3
from boto3.dynamodb.conditions import Key
import os
import json
from datetime import datetime
import decimal

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return str(obj)
        return super(DecimalEncoder, self).default(obj)


def generate_error_response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}),
        "headers": {"Content-Type": "application/json"},
    }


# Helper function to get the latest version number
def get_latest_version_number(table):
    # Query the table for the latest item
    response = table.query(
        KeyConditionExpression=Key("key").eq("latest"),
        ScanIndexForward=False,  # Sorts the versions in descending order
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return 0  # Return zero to indicate that there is no latest version
    latest_version = int(items[0]["version"])
    return latest_version


# Helper function to get the latest version details
def get_latest_version_details(table):
    response = table.query(
        KeyConditionExpression=Key("key").eq("latest"),
        ScanIndexForward=False,  # Sorts the versions in descending order
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None  # Return None to indicate that there is no latest version
    latest_version_details = items[0]
    return latest_version_details


# Function to upload a new data disclosure version
def upload_data_disclosure(event, context):
    # Define the local file path and S3 bucket name
    local_file_path = "data_disclosure.md"
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]
    versions_table_name = os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"]

    # Attempt to read the document content from the local file
    try:
        with open(local_file_path, "rb") as document_file:
            document_content = document_file.read()
    except FileNotFoundError:
        return generate_error_response(404, "Data disclosure file not found")
    except IOError as e:
        return generate_error_response(500, "Error reading local data disclosure file")

    # Generate the document name using the current date
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    document_name = f"data_disclosure_{timestamp}.md"

    try:
        # Upload the document to the S3 bucket
        s3.put_object(Bucket=bucket_name, Key=document_name, Body=document_content)
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error saving data disclosure to S3")

    versions_table = dynamodb.Table(versions_table_name)

    # Get the latest version number from the DataDisclosureVersionsTable
    latest_version = get_latest_version_number(versions_table)

    # Increment on the latest version
    new_version = latest_version + 1

    # Save the new version information in the DataDisclosureVersionsTable
    try:
        # Always use the same key for the latest version
        versions_table.put_item(
            Item={
                "key": "latest",
                "version": new_version,
                "id": document_name,
                "timestamp": timestamp,
                "s3_reference": f"s3://{bucket_name}/{document_name}",
            }
        )
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Data disclosure uploaded successfully"}),
        }
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error uploading data disclosure")


# Check if a user's email has accepted the agreement in the DataDisclosureAcceptanceTable
def check_data_disclosure_decision(event, context):
    query_params = event.get("queryStringParameters") or {}
    email = query_params.get("email")
    if not email:
        return generate_error_response(400, "Missing email parameter")

    table = dynamodb.Table(os.environ["DATA_DISCLOSURE_ACCEPTANCE_TABLE"])

    try:
        response = table.get_item(Key={"user": email})
        acceptedDataDisclosure = "Item" in response and response["Item"].get(
            "acceptedDataDisclosure", False
        )
        return {
            "statusCode": 200,
            "body": json.dumps({"acceptedDataDisclosure": acceptedDataDisclosure}),
        }
    except Exception as e:
        print(e)
        return generate_error_response(
            500, "Error checking data disclosure acceptance status"
        )


# Save the user's acceptance or denial of the data disclosure in the DataDisclosureAcceptanceTable
def save_data_disclosure_decision(event, context):
    try:
        body = json.loads(event["body"])
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON format"})}

    email = body.get("email")
    accepted_data_disclosure = body.get("acceptedDataDisclosure")

    if not isinstance(email, str) or accepted_data_disclosure not in (True, False):
        return generate_error_response(
            400, "Invalid input for saving data disclosure acceptance"
        )

    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    acceptance_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_ACCEPTANCE_TABLE"])

    # Get the latest version details from the DataDisclosureVersionsTable
    latest_version_details = get_latest_version_details(versions_table)
    if not latest_version_details:
        return generate_error_response(
            500, "Error retrieving latest data disclosure version"
        )

    try:
        acceptance_table.put_item(
            Item={
                "user": email,
                "acceptedDataDisclosure": accepted_data_disclosure,
                "acceptedTimestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "completedTraining": False,
                "documentVersion": latest_version_details["version"],
                "documentID": latest_version_details["id"],
            }
        )
        return {"statusCode": 200, "body": json.dumps({"message": "Record saved"})}
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error saving data disclosure acceptance")


# Pull the most recent data disclosure from the DataDisclosureVersionsTable
def get_latest_data_disclosure(event, context):
    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]

    try:
        # Query the table for the latest item
        latest_version_details = get_latest_version_details(versions_table)
        if not latest_version_details:
            # Handle the case where no latest agreement exists
            return generate_error_response(404, "No latest data disclosure found")

        # Fetch the content from the S3 bucket
        document_name = latest_version_details["id"]
        s3_response = s3.get_object(Bucket=bucket_name, Key=document_name)
        document_content = s3_response["Body"].read().decode("utf-8")

        # Include the content in the response
        latest_agreement = latest_version_details
        latest_agreement["content"] = document_content

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"latest_agreement": latest_agreement}, cls=DecimalEncoder
            ),
            "headers": {"Content-Type": "application/json"},
        }
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error collecting latest data disclosure")
