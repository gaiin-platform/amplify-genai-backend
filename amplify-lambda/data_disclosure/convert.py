"""
Data disclosure PDF conversion using markitdown library.
Converts PDF to markdown, then to HTML for storage in DynamoDB.
"""

import boto3
import json
import os
from datetime import datetime
from pycommon.logger import getLogger
from .markdown_to_html import markdown_to_html

logger = getLogger("data_disclosure_convert")


def generate_error_response(status_code, message):
    """Generate standardized error response."""
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}),
        "headers": {"Content-Type": "application/json"},
    }


def get_latest_version_details(table):
    """Helper function to get the latest version details from DynamoDB."""
    from boto3.dynamodb.conditions import Key
    
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


def convert_pdf_with_markitdown(pdf_local_path):
    """
    Convert PDF to HTML using markitdown library.
    
    Args:
        pdf_local_path (str): Path to local PDF file
        
    Returns:
        str: HTML content or error response dict
    """
    try:
        from markitdown import MarkItDown
        
        # Initialize markitdown
        md = MarkItDown()
        
        # Convert PDF to markdown
        logger.info(f"Converting PDF {pdf_local_path} to markdown using markitdown")
        result = md.convert(pdf_local_path)
        
        if not result or not result.text_content:
            logger.error("Markitdown returned empty content")
            return generate_error_response(500, "PDF conversion resulted in empty content")
        
        markdown_content = result.text_content
        logger.info(f"Successfully converted PDF to markdown ({len(markdown_content)} characters)")
        
        # Convert markdown to HTML using our custom converter
        html_content = markdown_to_html(markdown_content)
        
        if not html_content:
            logger.error("Markdown to HTML conversion resulted in empty content")
            return generate_error_response(500, "Markdown to HTML conversion failed")
        
        logger.info(f"Successfully converted markdown to HTML ({len(html_content)} characters)")
        return html_content
        
    except ImportError as e:
        logger.error(f"Failed to import markitdown: {e}")
        return generate_error_response(500, "Markitdown library not available")
    except Exception as e:
        logger.error(f"Error converting PDF with markitdown: {e}")
        return generate_error_response(500, f"PDF conversion failed: {str(e)}")


def convert_uploaded_data_disclosure(event, context):
    """
    Lambda handler for converting uploaded data disclosure PDFs.
    
    This function:
    1. Downloads PDF from S3 consolidation bucket
    2. Converts PDF to markdown using markitdown
    3. Converts markdown to HTML using custom converter  
    4. Stores result in DATA_DISCLOSURE_VERSIONS_TABLE with same format as original
    
    Args:
        event: S3 trigger event
        context: Lambda context
        
    Returns:
        dict: Response with status and message
    """
    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")
    
    # Get environment variables
    consolidation_bucket_name = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
    versions_table_name = os.environ.get("DATA_DISCLOSURE_VERSIONS_TABLE")
    
    if not consolidation_bucket_name or not versions_table_name:
        logger.error("Missing required environment variables")
        return generate_error_response(500, "Missing required environment variables")
    
    try:
        # Parse S3 event
        record = event["Records"][0]
        pdf_key = record["s3"]["object"]["key"]
    except (IndexError, KeyError) as e:
        logger.error(f"Error parsing event: {e}")
        return generate_error_response(400, "Invalid event format, cannot find PDF key")
    
    logger.info(f"Processing data disclosure PDF: {pdf_key}")
    
    # Check if this file has already been processed (idempotency check)
    versions_table = dynamodb.Table(versions_table_name)
    
    try:
        # Check if a record with this pdf_id already exists
        response = versions_table.scan(
            FilterExpression="pdf_id = :pdf_id",
            ExpressionAttributeValues={":pdf_id": pdf_key},
            Limit=1
        )
        
        if response.get("Items"):
            existing_item = response["Items"][0]
            # Convert Decimal to int for JSON serialization - existing record MUST have valid version
            existing_version = int(existing_item["version"])  
            existing_timestamp = str(existing_item["timestamp"])  # Convert to string
            logger.info(f"File {pdf_key} already processed as version {existing_version}, skipping")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "File already processed, skipping",
                    "existing_version": existing_version,
                    "timestamp": existing_timestamp
                }),
            }
    except Exception as e:
        logger.warning(f"Could not check for existing record: {e}. Proceeding with processing.")
    
    logger.info("File not previously processed, proceeding with conversion")
    # Extract timestamp from filename - handle dataDisclosure/ prefix
    filename = pdf_key.replace("dataDisclosure/", "")
    prefix = "data_disclosure_"
    suffix = ".pdf"
    
    if filename.startswith(prefix) and filename.endswith(suffix):
        timestamp = filename[len(prefix) : -len(suffix)]
    else:
        logger.error(f"Filename {filename} is not in expected format")
        return generate_error_response(400, "PDF filename is not in expected format")
    
    logger.debug(f"Extracted timestamp: {timestamp}")
    
    # Download PDF from S3
    pdf_local_path = "/tmp/input.pdf"
    
    try:
        logger.info(f"Downloading PDF from s3://{consolidation_bucket_name}/{pdf_key}")
        s3.download_file(consolidation_bucket_name, pdf_key, pdf_local_path)
        logger.info(f"File downloaded successfully to {pdf_local_path}")
    except Exception as e:
        logger.error(f"Error downloading PDF from S3: {e}")
        return generate_error_response(500, "Error downloading PDF from S3")
    
    if not os.path.exists(pdf_local_path):
        logger.error(f"File not found at {pdf_local_path} after download")
        return generate_error_response(500, "File download failed")
    
    # Convert PDF to HTML using markitdown
    html_content = convert_pdf_with_markitdown(pdf_local_path)
    if not isinstance(html_content, str):
        # html_content is an error response dict
        return html_content
    
    # Update DynamoDB with new version info - same format as original function
    logger.info("Updating DynamoDB with new version info")
    versions_table = dynamodb.Table(versions_table_name)
    
    # Get latest version to increment
    latest_version_details = get_latest_version_details(versions_table)
    new_version = (
        0 if not latest_version_details else int(latest_version_details["version"]) + 1
    )
    logger.info(f"New version number: {new_version}")
    
    # Save the new version information in the DataDisclosureVersionsTable
    # Using exact same format as original function
    try:
        versions_table.put_item(
            Item={
                "key": "latest",
                "version": new_version,
                "pdf_id": pdf_key,
                "html_content": html_content,
                "timestamp": timestamp,
                "s3_reference": f"s3://{consolidation_bucket_name}/{pdf_key}",
            }
        )
        
        logger.info(f"Successfully saved version {new_version} to DynamoDB")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Data disclosure uploaded successfully",
                "version": new_version,
                "timestamp": timestamp
            }),
        }
        
    except Exception as e:
        logger.error(f"Error saving to DynamoDB: {e}")
        return generate_error_response(500, "Error uploading data disclosure to database")