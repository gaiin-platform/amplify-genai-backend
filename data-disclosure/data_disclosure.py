from io import BytesIO
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import os
import json
from datetime import datetime
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, S3Operation
)
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

from pycommon.logger import getLogger
logger = getLogger("data_disclosure")

def generate_error_response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}),
        "headers": {"Content-Type": "application/json"},
    }


def _parse_s3_reference_for_pdf_access(s3_reference, pdf_document_name, consolidation_bucket_name):
    """
    Smart bucket detection with actual file existence checking for backward compatibility.
    
    Tries multiple locations in order and returns the first one where the file actually exists:
    1. Parsed s3_reference location (if valid S3 URI)
    2. Consolidation bucket with dataDisclosure/ prefix
    3. Old DATA_DISCLOSURE_STORAGE_BUCKET (backward compatibility fallback)
    
    Args:
        s3_reference (str): Full S3 URI from DynamoDB record
        pdf_document_name (str): PDF filename from pdf_id field
        consolidation_bucket_name (str): Current consolidation bucket name
        
    Returns:
        tuple: (bucket_name, key) for S3 access where file actually exists
    """
    logger.debug(f"Finding S3 location for pdf_id='{pdf_document_name}', s3_reference='{s3_reference}'")
    
    def check_file_exists(bucket, key):
        """Check if file exists in S3 bucket"""
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False
    
    locations_to_try = []
    
    # Location 1: Parse s3_reference if it's a valid S3 URI
    if s3_reference and s3_reference.startswith("s3://"):
        try:
            s3_uri = s3_reference[5:]  # Remove 's3://'
            bucket_name, key = s3_uri.split("/", 1)
            locations_to_try.append((bucket_name, key, "s3_reference"))
        except ValueError:
            logger.warning(f"Invalid s3_reference format: {s3_reference}")
    
    # Location 2: Consolidation bucket with dataDisclosure/ prefix (new migrated location)
    if pdf_document_name:
        if pdf_document_name.startswith("dataDisclosure/"):
            # Already has prefix
            consolidation_key = pdf_document_name
        else:
            # Add prefix for migrated files
            consolidation_key = f"dataDisclosure/{pdf_document_name}"
        locations_to_try.append((consolidation_bucket_name, consolidation_key, "consolidation_bucket"))
    
    # Location 3: Old DATA_DISCLOSURE_STORAGE_BUCKET (backward compatibility)
    old_storage_bucket = os.environ.get("DATA_DISCLOSURE_STORAGE_BUCKET")
    if old_storage_bucket and pdf_document_name:
        # Old bucket used original filename without prefix
        old_key = pdf_document_name.replace("dataDisclosure/", "") if pdf_document_name.startswith("dataDisclosure/") else pdf_document_name
        locations_to_try.append((old_storage_bucket, old_key, "old_storage_bucket"))
    
    # Try each location until we find the file
    for bucket, key, source in locations_to_try:
        if check_file_exists(bucket, key):
            if source == "old_storage_bucket":
                logger.info(f"📁 Data disclosure PDF found in OLD BUCKET (not yet migrated): s3://{bucket}/{key}")
            elif source == "consolidation_bucket":
                logger.info(f"✅ Data disclosure PDF found in CONSOLIDATION BUCKET (migrated): s3://{bucket}/{key}")
            else:
                logger.info(f"📄 Data disclosure PDF found via s3_reference: s3://{bucket}/{key}")
            return bucket, key
    
    # If file not found anywhere, return the first preference (parsed s3_reference or consolidation)
    if locations_to_try:
        bucket, key, source = locations_to_try[0]
        logger.warning(f"File not found at any location, using first preference ({source}): s3://{bucket}/{key}")
        return bucket, key
    
    # Ultimate fallback
    logger.error(f"No valid locations to try for pdf_document_name='{pdf_document_name}'")
    return consolidation_bucket_name, pdf_document_name or ""


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


@required_env_vars({
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT],
})
@validated(op="upload")
def get_presigned_data_disclosure(event, context, current_user, name, data):
    # Authorize the User
    if not verify_user_as_admin(data["access_token"], "Upload Data Disclosure"):
        return {"success": False, "message": "User is not an authorized admin."}
    data = data["data"]
    content_md5 = data.get("md5")
    content_type = data.get("contentType")
    fileKey = f"dataDisclosure/{data.get('fileName')}"

    config = Config(signature_version="s3v4")  # Force AWS Signature Version 4
    s3_client = boto3.client("s3", config=config)
    consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
 
    try:
        # Generate a presigned URL for put_object
        logger.info("Presigned url generated")
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": consolidation_bucket_name,
                "Key": fileKey,
                "ContentType": content_type,
                "ContentMD5": content_md5,
            },
            ExpiresIn=3600,  # URL expires in 1 hour
        )

        logger.debug("Presigned url: %s", presigned_url)

        return {"success": True, "presigned_url": presigned_url}
    except ClientError as e:
        logger.error(f"Error generating presigned URL: {str(e)}")
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
        logger.error(f"Error converting PDF to HTML: {e}")
        return generate_error_response(500, "Error converting PDF to HTML")




# helper function to get the latest version number
def get_latest_version_number():
    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    latest_version_details = get_latest_version_details(versions_table)
    return latest_version_details["version"] if latest_version_details else None


# Check if a user's email has accepted the agreement in the DataDisclosureAcceptanceTable
@required_env_vars({
    "DATA_DISCLOSURE_ACCEPTANCE_TABLE": [DynamoDBOperation.GET_ITEM],
    "DATA_DISCLOSURE_VERSIONS_TABLE": [DynamoDBOperation.QUERY],
})
@validated(op="check_data_disclosure_decision")
def check_data_disclosure_decision(event, context, current_user, name, data):
    table = dynamodb.Table(os.environ["DATA_DISCLOSURE_ACCEPTANCE_TABLE"])

    try:
        # Get the latest version number
        latest_version = get_latest_version_number()
        if latest_version is None:
            logger.error("Error retrieving latest data disclosure version")
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
        logger.error(str(e))
        return generate_error_response(
            500, "Error checking data disclosure acceptance status"
        )


# Save the user's acceptance or denial of the data disclosure in the DataDisclosureAcceptanceTable
@required_env_vars({
    "DATA_DISCLOSURE_VERSIONS_TABLE": [DynamoDBOperation.QUERY],
    "DATA_DISCLOSURE_ACCEPTANCE_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="save_data_disclosure_decision")
def save_data_disclosure_decision(event, context, current_user, name, data):
    data = data["data"]
    accepted_data_disclosure = data.get("acceptedDataDisclosure")

    if not isinstance(current_user, str) or accepted_data_disclosure not in (True, False):
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
                "user": current_user,
                "acceptedDataDisclosure": accepted_data_disclosure,
                "acceptedTimestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "completedTraining": False,
                "documentVersion": latest_version_details["version"],
                "documentID": latest_version_details["s3_reference"],
            }
        )
        return {"statusCode": 200, "body": json.dumps({"message": "Record saved"})}
    except Exception as e:
        logger.error(str(e))
        return generate_error_response(500, "Error saving data disclosure acceptance")


# Pull the most recent data disclosure from the DataDisclosureVersionsTable
@required_env_vars({
    "DATA_DISCLOSURE_VERSIONS_TABLE": [DynamoDBOperation.QUERY],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "DATA_DISCLOSURE_STORAGE_BUCKET": [S3Operation.GET_OBJECT],  # Marked for future deletion - backward compatibility
})
@validated(op="get_latest_data_disclosure")
def get_latest_data_disclosure(event, context, current_user, name, data):
    versions_table = dynamodb.Table(os.environ["DATA_DISCLOSURE_VERSIONS_TABLE"])
    consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]

    try:
        latest_version_details = get_latest_version_details(versions_table)
        if not latest_version_details:
            return generate_error_response(404, "No latest data disclosure found")

        pdf_document_name = latest_version_details["pdf_id"]
        html_content = latest_version_details.get("html_content")
        s3_reference = latest_version_details.get("s3_reference", "")

        # Smart bucket detection for backward compatibility
        bucket_name, key = _parse_s3_reference_for_pdf_access(
            s3_reference, pdf_document_name, consolidation_bucket_name
        )

        # Generate a pre-signed URL for the PDF document
        try:
            pdf_pre_signed_url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket_name,
                    "Key": key,
                },
                ExpiresIn=360,  # URL expires in 6 mins
            )
        except ClientError as e:
            logger.error(f"Error generating PDF pre-signed URL for {bucket_name}/{key}: {str(e)}")
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
        logger.error(str(e))
        return generate_error_response(500, "Error collecting latest data disclosure")
