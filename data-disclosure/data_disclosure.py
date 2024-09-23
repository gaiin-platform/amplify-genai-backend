import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import os
import json
from datetime import datetime
from common.validate import validated
from common.encoders import DecimalEncoder

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")


def generate_error_response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}),
        "headers": {"Content-Type": "application/json"},
    }


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
    # Define the local file paths and S3 bucket name
    local_pdf_path = "data_disclosure.pdf"
    local_html_path = "data_disclosure.html"
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]
    versions_table_name = os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"]

    # Read the PDF and HTML document content
    try:
        with open(local_pdf_path, "rb") as pdf_file:
            pdf_content = pdf_file.read()
    except FileNotFoundError:
        return generate_error_response(404, "Data disclosure PDF file not found")
    except IOError as e:
        return generate_error_response(
            500, "Error reading local data disclosure PDF file"
        )

    try:
        with open(local_html_path, "rb") as html_file:
            html_content = html_file.read()
        # Attempt to decode as UTF-8, but handle exceptions
        try:
            html_content = html_content.decode("utf-8")  # if this fails, try utf-16
        except UnicodeDecodeError as e:
            print(f"Error decoding HTML content: {e}")
            # Handle the error, e.g., by skipping the decoding or using a different encoding
            return generate_error_response(500, "Error decoding HTML content")
    except FileNotFoundError:
        return generate_error_response(404, "Data disclosure HTML file not found")
    except IOError as e:
        return generate_error_response(
            500, "Error reading local data disclosure HTML file"
        )

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    html_document_name = f"data_disclosure_{timestamp}.html"
    pdf_document_name = f"data_disclosure_{timestamp}.pdf"

    try:
        # Upload PDF version
        s3.put_object(
            Bucket=bucket_name,
            Key=pdf_document_name,
            Body=pdf_content,
            ContentType="application/pdf",
        )
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error saving pdf data disclosure to S3")

    # Update DynamoDB with new version info, including references to both HTML and PDF
    versions_table = dynamodb.Table(versions_table_name)
    latest_version_details = get_latest_version_details(versions_table)
    new_version = (
        0 if not latest_version_details else int(latest_version_details["version"]) + 1
    )

    # Save the new version information in the DataDisclosureVersionsTable
    try:
        # Always use the same key for the latest version
        versions_table.put_item(
            Item={
                "key": "latest",
                "version": new_version,
                "pdf_id": pdf_document_name,
                "html_content": html_content,
                "timestamp": timestamp,
                "s3_reference": f"s3://{bucket_name}/{pdf_document_name}",
            }
        )
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Data disclosure uploaded successfully"}),
        }
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error uploading data disclosure")


# helper function to get the latest version number
def get_latest_version_number():
    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    latest_version_details = get_latest_version_details(versions_table)
    return latest_version_details["version"] if latest_version_details else None


# Check if a user's email has accepted the agreement in the DataDisclosureAcceptanceTable
@validated(op="check_data_disclosure_decision")
def check_data_disclosure_decision(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters") or {}
    email = query_params.get("email")
    if not email:
        return generate_error_response(400, "Missing email parameter")

    table = dynamodb.Table(os.environ["DATA_DISCLOSURE_ACCEPTANCE_TABLE"])

    try:
        # Get the latest version number
        latest_version = get_latest_version_number()
        if latest_version is None:
            return generate_error_response(
                500, "Error retrieving latest data disclosure version"
            )

        # Get the user's acceptance record
        response = table.get_item(Key={"user": email})

        if "Item" in response:
            user_accepted = response["Item"].get("acceptedDataDisclosure", False)
            user_version = response["Item"].get("documentVersion")

            # Check if the user accepted the latest version
            acceptedDataDisclosure = user_accepted and (user_version == latest_version)
        else:
            acceptedDataDisclosure = False

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
@validated(op="save_data_disclosure_decision")
def save_data_disclosure_decision(event, context, current_user, name, data):
    data = data['data']
    email = data.get("email")
    accepted_data_disclosure = data.get("acceptedDataDisclosure")

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
                "documentID": latest_version_details["s3_reference"],
            }
        )
        return {"statusCode": 200, "body": json.dumps({"message": "Record saved"})}
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error saving data disclosure acceptance")


# Pull the most recent data disclosure from the DataDisclosureVersionsTable
@validated(op="get_latest_data_disclosure")
def get_latest_data_disclosure(event, context, current_user, name, data):
    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]

    try:
        latest_version_details = get_latest_version_details(versions_table)
        if not latest_version_details:
            return generate_error_response(404, "No latest data disclosure found")

        pdf_document_name = latest_version_details["pdf_id"]
        html_content = latest_version_details.get("html_content")

        # Generate a pre-signed URL for the PDF document
        try:
            pdf_pre_signed_url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket_name,
                    "Key": pdf_document_name,
                },
                ExpiresIn=360,  # URL expires in 6 mins
            )
        except ClientError as e:
            print(e)
            return generate_error_response(500, "Error generating PDF pre-signed URL")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "latest_agreement": latest_version_details,
                    "pdf_pre_signed_url": pdf_pre_signed_url,
                    "html_content": html_content,
                },
                cls=DecimalEncoder,
            ),
            "headers": {"Content-Type": "application/json"},
        }
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error collecting latest data disclosure")
