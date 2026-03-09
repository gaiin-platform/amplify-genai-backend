# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import re
import boto3
from botocore.exceptions import ClientError
from pycommon.authz import validated, setup_validated, add_api_access_types
from pycommon.const import APIAccessType
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import SecretsManagerOperation
from pycommon.api.ops import api_tool
from pycommon.logger import getLogger
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

add_api_access_types([APIAccessType.CHAT.value])
setup_validated(rules, get_permission_checker)
logger = getLogger("bedrock_kb_download")

s3_client = boto3.client("s3")
bedrock_agent_client = boto3.client("bedrock-agent")

# Cache KB ID -> set of allowed bucket names to avoid repeated Bedrock API calls
_kb_bucket_cache = {}


def get_allowed_buckets_for_kb(knowledge_base_id):
    """Look up the S3 buckets configured as data sources for a Bedrock Knowledge Base.

    Calls ListDataSources to get all data source IDs, then GetDataSource for each
    to extract the S3 bucket ARN from the configuration.

    Results are cached per KB ID for the lifetime of the Lambda container.

    Args:
        knowledge_base_id: The Bedrock Knowledge Base ID.

    Returns:
        set: A set of bucket names that are configured as data sources for this KB.
    """
    if knowledge_base_id in _kb_bucket_cache:
        return _kb_bucket_cache[knowledge_base_id]

    allowed_buckets = set()
    try:
        # List all data sources for this KB
        paginator = bedrock_agent_client.get_paginator("list_data_sources")
        for page in paginator.paginate(knowledgeBaseId=knowledge_base_id):
            for ds_summary in page.get("dataSourceSummaries", []):
                ds_id = ds_summary["dataSourceId"]
                try:
                    ds_response = bedrock_agent_client.get_data_source(
                        knowledgeBaseId=knowledge_base_id,
                        dataSourceId=ds_id,
                    )
                    ds_config = ds_response.get("dataSource", {}).get(
                        "dataSourceConfiguration", {}
                    )
                    s3_config = ds_config.get("s3Configuration", {})
                    bucket_arn = s3_config.get("bucketArn", "")
                    if bucket_arn:
                        # Extract bucket name from ARN: arn:aws:s3:::bucket-name
                        bucket_name = bucket_arn.split(":::")[-1]
                        allowed_buckets.add(bucket_name)
                        logger.info(
                            "KB %s data source %s uses bucket: %s",
                            knowledge_base_id, ds_id, bucket_name,
                        )
                except Exception as e:
                    logger.error(
                        "Error getting data source %s for KB %s: %s",
                        ds_id, knowledge_base_id, str(e),
                    )
    except Exception as e:
        logger.error(
            "Error listing data sources for KB %s: %s",
            knowledge_base_id, str(e),
        )
        return allowed_buckets

    _kb_bucket_cache[knowledge_base_id] = allowed_buckets
    logger.info(
        "Cached %d allowed buckets for KB %s: %s",
        len(allowed_buckets), knowledge_base_id, allowed_buckets,
    )
    return allowed_buckets


def parse_s3_uri(s3_uri):
    """Parse an S3 URI into bucket and key components.

    Args:
        s3_uri: An S3 URI in the format s3://bucket-name/path/to/object

    Returns:
        tuple: (bucket_name, object_key) or (None, None) if invalid.
    """
    match = re.match(r"^s3://([^/]+)/(.+)$", s3_uri)
    if match:
        return match.group(1), match.group(2)
    return None, None


def validate_s3_uri_against_kb(knowledge_base_id, s3_uri):
    """Validate that an S3 URI belongs to a bucket configured in the given KB.

    Args:
        knowledge_base_id: The Bedrock Knowledge Base ID.
        s3_uri: The S3 URI to validate.

    Returns:
        dict: {"valid": bool, "bucket": str|None, "key": str|None, "message": str}
    """
    bucket, key = parse_s3_uri(s3_uri)
    if not bucket or not key:
        return {
            "valid": False,
            "bucket": None,
            "key": None,
            "message": f"Invalid S3 URI format: {s3_uri}",
        }

    allowed_buckets = get_allowed_buckets_for_kb(knowledge_base_id)
    if not allowed_buckets:
        return {
            "valid": False,
            "bucket": bucket,
            "key": key,
            "message": f"Could not retrieve data source configuration for KB {knowledge_base_id}",
        }

    if bucket not in allowed_buckets:
        logger.warning(
            "Bucket %s not in allowed buckets %s for KB %s",
            bucket, allowed_buckets, knowledge_base_id,
        )
        return {
            "valid": False,
            "bucket": bucket,
            "key": key,
            "message": f"S3 bucket '{bucket}' is not a configured data source for KB {knowledge_base_id}",
        }

    return {"valid": True, "bucket": bucket, "key": key, "message": "Valid"}


@api_tool(
    path="/bedrock-kb/download",
    name="bedrockKbDownload",
    method="POST",
    tags=["apiDocumentation"],
    description="""Generate a presigned download URL for a file from a Bedrock Knowledge Base's S3 data source.

    Validates that the requested S3 URI belongs to a bucket configured as a data source
    for the specified Knowledge Base before generating the presigned URL.

    Example request:
    {
        "data": {
            "knowledgeBaseId": "ABCDEFGHIJ",
            "s3Uri": "s3://my-kb-bucket/documents/guide.pdf"
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "knowledgeBaseId": {
                "type": "string",
                "description": "The Bedrock Knowledge Base ID.",
            },
            "s3Uri": {
                "type": "string",
                "description": "The S3 URI of the file to download (e.g., s3://bucket/key).",
            },
        },
        "required": ["knowledgeBaseId", "s3Uri"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "downloadUrl": {"type": "string", "description": "Presigned S3 download URL"},
            "fileName": {"type": "string", "description": "The file name extracted from the S3 key"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "APP_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
})
@validated("bedrock-kb-download")
def bedrock_kb_download(event, context, current_user, name, data):
    """Generate a presigned URL for downloading a file from a Bedrock KB's S3 data source."""
    access = data["allowed_access"]
    if APIAccessType.CHAT.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        return {
            "success": False,
            "message": "API key does not have access to this functionality",
        }

    request_data = data["data"]
    knowledge_base_id = request_data.get("knowledgeBaseId", "").strip()
    s3_uri = request_data.get("s3Uri", "").strip()

    if not knowledge_base_id:
        return {"success": False, "message": "knowledgeBaseId is required"}
    if not s3_uri:
        return {"success": False, "message": "s3Uri is required"}

    # Validate the S3 URI belongs to this KB's configured buckets
    validation = validate_s3_uri_against_kb(knowledge_base_id, s3_uri)
    if not validation["valid"]:
        return {"success": False, "message": validation["message"]}

    bucket = validation["bucket"]
    key = validation["key"]
    file_name = key.split("/")[-1] if "/" in key else key

    try:
        # Verify the object exists
        s3_client.head_object(Bucket=bucket, Key=key)

        # Generate presigned URL (valid for 1 hour)
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{file_name}"',
            },
            ExpiresIn=3600,
        )

        logger.info(
            "Generated presigned URL for KB %s file %s/%s for user %s",
            knowledge_base_id, bucket, key, current_user,
        )

        return {
            "success": True,
            "downloadUrl": presigned_url,
            "fileName": file_name,
            "message": "Download URL generated successfully",
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "404" or error_code == "NoSuchKey":
            return {"success": False, "message": f"File not found: {s3_uri}"}
        logger.error("S3 error generating presigned URL: %s", str(e))
        return {"success": False, "message": f"Error accessing file: {str(e)}"}
    except Exception as e:
        logger.error("Error generating presigned URL: %s", str(e))
        return {"success": False, "message": f"Error generating download URL: {str(e)}"}
