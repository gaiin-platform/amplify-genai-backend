import subprocess
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import os
import json
from datetime import datetime

import mammoth
from common.validate import validated
from common.encoders import DecimalEncoder
from common.auth_admin import verify_user_as_admin

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



@validated(op="upload")
def get_presigned_data_disclosure(event, context, current_user, name, data):
    # Authorize the User
    if not verify_user_as_admin(data['access_token'], "Upload Data Disclosure"):
        return {"success": False, "message": "User is not an authorized admin."}
    content_md5 = data.get('md5', '')

    s3_client = boto3.client('s3')
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]
    key = f"data_disclosure.docx"

    try:
        # Generate a presigned URL for put_object
        print("Presigned url generated")
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                'ContentType': "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                'ContentMD5': content_md5
            },
            ExpiresIn=3600  # URL expires in 1 hour
        )

        print("\n", presigned_url)

        return {"success": True, "presigned_url": presigned_url}
    except ClientError as e:
        print(f"Error generating presigned URL: {str(e)}")
        return {"success": False, "message": f"Error generating presigned URL: {str(e)}"}




def convert_uploaded_data_disclosure(event, context):
    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")

    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]
    versions_table_name = os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"]

    docx_key = "data_disclosure.docx"
    download_path = "/tmp/data_disclosure.docx"
    output_dir = "/tmp"

    try:
        print("Downloading uploaded file")
        s3.download_file(bucket_name, docx_key, download_path)
    except Exception as e:
        print(f"Error downloading docx file from S3: {e}")
        return generate_error_response(404, "Data disclosure docx file not found in S3")

    # Convert DOCX to PDF using LibreOffice
    # LibreOffice command: /usr/bin/libreoffice --headless --convert-to pdf --outdir /tmp /tmp/data_disclosure.docx
    try:
        print("converting file to pdf file")

        subprocess.check_call([
            '/usr/bin/libreoffice',
            '--headless',
            '--convert-to',
            'pdf',
            '--outdir',
            output_dir,
            download_path
        ])
    except subprocess.CalledProcessError as e:
        print(f"Error converting docx to pdf: {e}")
        return generate_error_response(500, "Error converting docx to PDF")

    # The converted PDF will be named "data_disclosure.pdf" in /tmp if the input was "data_disclosure.docx"
    print("converting file to pdf file")
    pdf_local_path = os.path.join(output_dir, "data_disclosure.pdf")
    if not os.path.exists(pdf_local_path):
        return generate_error_response(500, "PDF conversion failed, output file not found")

    #Convert DOCX to HTML using Mammoth
    # Mammoth expects a file-like object with .read()
    try:
        print("converting file to html file")
        with open(download_path, "rb") as docx_file:
            result = mammoth.convert_to_html(docx_file)
            html_content = result.value  # HTML as a string
            print("Html: ", html_content )
    except Exception as e:
        print(f"Error converting docx to html: {e}")
        return generate_error_response(500, "Error converting DOCX to HTML")

    # Create timestamp and generate unique filenames
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    pdf_document_name = f"data_disclosure_{timestamp}.pdf"

    try:
        print("uploading pdf format")
        s3.upload_file(pdf_local_path, bucket_name, pdf_document_name, ExtraArgs={"ContentType": "application/pdf"})
    except Exception as e:
        print(f"Error uploading pdf to S3: {e}")
        return generate_error_response(500, "Error saving PDF data disclosure to S3")


     # Update DynamoDB with new version info, including references to both HTML and PDF
    print("Update DynamoDB with new version info")
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
