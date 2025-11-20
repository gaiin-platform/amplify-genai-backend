from io import BytesIO
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import os
import json
from datetime import datetime
from pycommon.encoders import SafeDecimalEncoder
from pycommon.api.auth_admin import verify_user_as_admin
from botocore.config import Config
import fitz
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.DATA_DISCLOSURE.value])

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
    if not verify_user_as_admin(data["access_token"], "Upload Data Disclosure"):
        return {"success": False, "message": "User is not an authorized admin."}
    data = data["data"]
    content_md5 = data.get("md5")
    content_type = data.get("contentType")
    fileKey = data.get("fileName")

    config = Config(signature_version="s3v4")  # Force AWS Signature Version 4
    s3_client = boto3.client("s3", config=config)
    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]

    try:
        # Generate a presigned URL for put_object
        print("Presigned url generated")
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": fileKey,
                "ContentType": content_type,
                "ContentMD5": content_md5,
            },
            ExpiresIn=3600,  # URL expires in 1 hour
        )

        print("\n", presigned_url)

        return {"success": True, "presigned_url": presigned_url}
    except ClientError as e:
        print(f"Error generating presigned URL: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating presigned URL: {str(e)}",
        }


def convert_pdf(pdf_local_path):
    try:
        doc = fitz.open(pdf_local_path)
        page_contents = []
        for page in doc:
            page_text = page.get_text("text")
            # Convert the text into <p class="MsoNormal"> paragraphs
            # Split by newlines and wrap each line in a <p>
            lines = page_text.split("\n\n")
            formatted_lines = [
                f'<p class=MsoNormal style="text-align:justify">{line}</p>'
                for line in lines
                if line.strip()
            ]
            page_html = (
                f"<h2 class=MsoNormal style='text-align:justify'><b>Page {page.number+1}</b></h2>"
                + "".join(formatted_lines)
            )
            page_contents.append(page_html)
        doc.close()

        # Combine pages
        combined_html = "\n".join(page_contents)

        # HTML Template (based on your provided sample)
        html_template = f"""
<html>

<head>
<meta http-equiv=Content-Type content="text/html; charset=utf-8">
<meta name=Generator content="Microsoft Word 15 (filtered)">
<style>
@font-face {{
    font-family:"Cambria Math";
    panose-1:2 4 5 3 5 4 6 3 2 4;
}}
@font-face {{
    font-family:Aptos;
    panose-1:2 11 0 4 2 2 2 2 2 4;
}}
p.MsoNormal, li.MsoNormal, div.MsoNormal {{
    margin-top:0in;
    margin-right:0in;
    margin-bottom:8.0pt;
    margin-left:0in;
    line-height:115%;
    font-size:12.0pt;
    font-family:"Times New Roman",serif;
}}
a:link, span.MsoHyperlink {{
    color:#467886;
    text-decoration:underline;
}}
.MsoChpDefault {{
    font-family:"Aptos",sans-serif;
}}
@page WordSection1 {{
    size:8.5in 11.0in;
    margin:1.0in 1.0in 1.0in 1.0in;
}}
div.WordSection1 {{
    page:WordSection1;
}}
</style>

</head>

<body lang=EN-US link="#467886" vlink="#96607D" style='word-wrap:break-word'>

<div class=WordSection1>
{combined_html}
</div>

</body>

</html>
"""
        return html_template

    except Exception as e:
        print(f"Error converting PDF to HTML: {e}")
        return generate_error_response(500, "Error converting PDF to HTML")


def convert_uploaded_data_disclosure(event, context):
    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")

    bucket_name = os.environ["DATA_DISCLOSURE_STORAGE_BUCKET"]
    versions_table_name = os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"]

    try:
        record = event["Records"][0]
        pdf_key = record["s3"]["object"]["key"]
    except (IndexError, KeyError) as e:
        print(f"Error parsing event: {e}")
        return generate_error_response(400, "Invalid event format, cannot find PDF key")

    # extract time stamp from dd name
    prefix = "data_disclosure_"
    suffix = ".pdf"

    if pdf_key.startswith(prefix) and pdf_key.endswith(suffix):
        timestamp = pdf_key[len(prefix) : -len(suffix)]
    else:
        raise ValueError("latest_dd_name is not in the expected format.")

    print(f"PDF Key: {pdf_key}")
    pdf_local_path = "/tmp/input.pdf"

    try:
        s3.download_file(bucket_name, pdf_key, pdf_local_path)
        print(f"File downloaded successfully to {pdf_local_path}")
    except Exception as e:
        print(f"Error downloading PDF from S3: {e}")
        return generate_error_response(500, "Error downloading PDF from S3")

    if not os.path.exists(pdf_local_path):
        print(f"File not found at {pdf_local_path} after download")
        return generate_error_response(500, "File download failed")

    html_content = convert_pdf(pdf_local_path)
    if not isinstance(html_content, str):
        return html_content

    # Update DynamoDB with new version info, including references to both HTML and PDF
    print("Update DynamoDB with new version info")
    versions_table = dynamodb.Table(versions_table_name)
    latest_version_details = get_latest_version_details(versions_table)
    new_version = (
        0 if not latest_version_details else int(latest_version_details["version"]) + 1
    )
    print("version number: ", new_version)

    # Save the new version information in the DataDisclosureVersionsTable
    try:
        # Always use the same key for the latest version
        versions_table.put_item(
            Item={
                "key": "latest",
                "version": new_version,
                "pdf_id": pdf_key,
                "html_content": html_content,
                "timestamp": timestamp,
                "s3_reference": f"s3://{bucket_name}/{pdf_key}",
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
    table = dynamodb.Table(os.environ["DATA_DISCLOSURE_ACCEPTANCE_TABLE"])

    try:
        # Get the latest version number
        latest_version = get_latest_version_number()
        if latest_version is None:
            print("Error retrieving latest data disclosure version")
            return generate_error_response(
                500, "Error retrieving latest data disclosure version"
            )

        # Get the user's acceptance record
        response = table.get_item(Key={"user": current_user})

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
    data = data["data"]
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
                cls=SafeDecimalEncoder,
            ),
            "headers": {"Content-Type": "application/json"},
        }
    except Exception as e:
        print(e)
        return generate_error_response(500, "Error collecting latest data disclosure")
