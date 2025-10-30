# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime
import uuid
from subprocess import check_output
import tempfile
from urllib.parse import unquote

# Required Libraries
import os
import boto3
from botocore.exceptions import NoCredentialsError
import os

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    S3Operation
)

from pycommon.logger import getLogger
logger = getLogger("docconverters")

setup_validated(rules, get_permission_checker)

# AWS S3 Client
s3 = boto3.client("s3")


def download_file_from_s3(bucket, key, file_path):
    try:
        s3.download_file(bucket, key, file_path)
        logger.info("Downloaded %s %s", bucket, key)
        return file_path
    except NoCredentialsError:
        logger.error("Credentials not available")
        return None


def upload_file_to_s3(bucket, key, file_path, content_type):
    try:
        s3.upload_file(
            file_path,
            bucket,
            key,
            ExtraArgs={"ACL": "private", "ContentType": content_type},
        )
        logger.info("Uploaded %s %s", bucket, key)
    except NoCredentialsError:
        logger.error("Credentials not available")


def parse_s3_key(s3_key):
    basename = s3_key.rsplit(".", 1)[0]
    extension = s3_key.rsplit(".", 1)[1]
    logger.debug("basename: %s, extension: %s", basename, extension)

    email = basename.split("/")[0]
    uuid_format = basename.split("/")[1]
    uuid = uuid_format.split("-to-")[0]
    fmat = uuid_format.split("-to-")[1]

    if email and uuid and fmat and extension:
        logger.debug("email: %s, uuid: %s, format: %s, extension: %s", email, uuid, fmat, extension)
        return email, uuid, fmat, extension
    else:
        return None, None, None, None
@required_env_vars({
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT, S3Operation.GET_OBJECT],
})
@validated("convert")
def submit_conversion_job(event, context, user, name, data):
    # URL decode the user parameter to ensure consistent key format
    from urllib.parse import unquote
    user = unquote(user)
    logger.info("User %s submitted conversion job", user)
    consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]

    data = data["data"]
    to_convert = data["content"]
    fmat = data["format"]
    conversation_header = data.get("conversationHeader", "# ")
    message_header = data.get("messageHeader", "")
    user_header = data.get("userHeader", "")
    assistant_header = data.get("assistantHeader", "")
    templateName = data.get("templateName", "")
    include_conversation_name = data.get("includeConversationName", False)

    # Initialize a list to store all message contents
    all_contents = []

    current_date = datetime.now()
    formatted_date = current_date.strftime("%B %d, %Y")

    # Loop over all conversations
    for conversation in to_convert["history"]:
        # Use the conversation header with conversation name
        metadata = ""
        if include_conversation_name:
            all_contents.append(f"{conversation_header}{conversation['name']}")
            metadata = f"""
---
title: {conversation['name']}
author:
  - {user}
date: {formatted_date}    
---
"""
            all_contents.append(metadata)

        # Loop over all messages in each conversation
        for message in conversation["messages"]:
            # Add appropriate role header before the content
            role_header = user_header if message["role"] == "user" else assistant_header
            all_contents.append(f"{message_header}{role_header}{message['content']}")

    # Join all contents into a single string
    contents_to_convert = "\n\n".join(all_contents)

    # Generate a unique UUID
    unique_uuid = str(uuid.uuid4())

    # Construct the key with consolidation bucket prefix
    consolidation_s3_key = f"conversion/input/{user}/{unique_uuid}-to-{fmat}.md"

    # Initialize the S3 client
    s3_client = boto3.client("s3")

    logger.info(
        "Saving file to S3 with key: %s and content type: text/markdown and bucket: %s", consolidation_s3_key, consolidation_bucket_name
    )
    # Save the contents to the consolidation bucket
    s3_client.put_object(
        Body=contents_to_convert,
        Bucket=consolidation_bucket_name,
        Key=consolidation_s3_key,
        ContentType="text/markdown",
    )

    logger.info("File saved to S3 with key: %s", consolidation_s3_key)

    if templateName and templateName != "":
        put_template_to_s3(user, unique_uuid, templateName)

    s3_output_key = f"conversion/output/{user}/{unique_uuid}.{fmat}"

    logger.debug("Creating presigned URL for user: '%s' (decoded)", user)
    logger.info("Returning presigned URL for key: %s and bucket: %s", s3_output_key, consolidation_bucket_name)

    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": consolidation_bucket_name, "Key": s3_output_key},
        ExpiresIn=3600,  # URL expires in 1 hour
    )

    # Create the response
    response = {
        "success": True,
        "message": "Document conversion started",
        "data": {"url": presigned_url},
    }

    return response


def put_template_to_s3(user: str, unique_uuid: str, template_name: str):
    try:
        s3_client = boto3.client("s3")
        consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]

        s3_template_key = f"conversion/output/{user}/{unique_uuid}.template"
        logger.info("Uploading template name '%s' to S3 with key: %s", template_name, s3_template_key)
        content = f"{template_name}"

        # Put the content string into S3
        s3_client.put_object(
            Body=content,
            Bucket=consolidation_bucket_name,
            Key=s3_template_key,
            ContentType="text/plain",
        )

        logger.info("Template name uploaded to S3 with key: %s", s3_template_key)
    except Exception as e:
        logger.error("Error uploading template to S3: %s, template will not be used", str(e))
        pass


def get_template_from_s3(user: str, unique_uuid: str):
    # Construct the template key
    s3_template_key = f"conversion/output/{user}/{unique_uuid}.template"

    # Initialize the S3 client
    s3_client = boto3.client("s3")

    consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]

    try:
        # Try to get the object from S3
        response = s3_client.get_object(Bucket=consolidation_bucket_name, Key=s3_template_key)

        # Read the content from the body
        data = response["Body"].read()

        # Decode the bytes to a string
        content = data.decode("utf-8")

        logger.info("Template fetched from S3 with key: %s", s3_template_key)

        return content

    except s3_client.exceptions.NoSuchKey:
        logger.warning("No template found in S3 with key: %s", s3_template_key)

    except Exception as e:
        logger.error("Error fetching template from S3: %s", str(e))

    return None


def handler(event, context):

    consolidation_bucket_name = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
    
    # Check if this is not a document conversion file
    for record in event["Records"]:
        key = unquote(record["s3"]["object"]["key"])
        
        # Only process files in conversion/input/ prefix
        if not key.startswith("conversion/input/"):
            logger.debug("Skipping non-conversion file: %s", key)
            return

    supported_mime_types = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "epub": "application/epub+zip",
        "html": "text/html",
        "latex": "application/x-latex",
        "markdown": "text/markdown",
        "odt": "application/vnd.oasis.opendocument.text",
        "pdf": "application/pdf",
        "rst": "text/x-rst",
        "rtf": "application/rtf",
        "tex": "application/x-tex",
        "txt": "text/plain",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xsml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
        "doc": "application/msword",
        "ipynb": "application/x-ipynb+json",
        "bib": "text/x-bibtex",
    }

    for record in event["Records"]:

        input_file = tempfile.NamedTemporaryFile(delete=False, suffix=".md")
        record = event["Records"][0]
        key = record["s3"]["object"]["key"]

        key = unquote(key)

        logger.info("Processing document conversion for key: %s", key)

        input_bucket = record["s3"]["bucket"]["name"]

        # Extract user path from consolidation bucket format
        # Consolidation bucket keys: conversion/input/{user}/{uuid}-to-{format}.md
        if not key.startswith("conversion/input/"):
            logger.error("Unexpected key format for document conversion: %s, expected conversion/input/ prefix", key)
            return

        user_path = key[len("conversion/input/"):]
        email, uuid, fmat, extension = parse_s3_key(user_path)

        logger.info("bucket:%s user: %s, uuid: %s, format: %s, extension: %s", input_bucket, email, uuid, fmat, extension)

        if not email or not uuid or not fmat or not extension:
            logger.error("Could not parse email, uuid, format, and extension from key: %s", key)
            return

        output_file = tempfile.NamedTemporaryFile(suffix="." + fmat, delete=False)

        mime_type = supported_mime_types.get(fmat, "text/plain")

        logger.debug("input_bucket: %s, key: %s, input_file: %s, output_file: %s, mime_type: %s", input_bucket, key, input_file.name, output_file.name, mime_type)

        download_file_from_s3(input_bucket, key, input_file.name)

        template_name = get_template_from_s3(email, uuid)
        has_template = False

        if template_name and template_name != "":
            try:
                template_key = f"conversion/templates/{template_name}"
                suffix = template_key.rsplit(".", 1)[1]
                logger.debug("template_key: %s, suffix: %s", template_key, suffix)
                template_file = tempfile.NamedTemporaryFile(
                    suffix="." + suffix, delete=False
                )
                download_file_from_s3(
                    consolidation_bucket_name, template_key, template_file.name
                )
                has_template = True
            except Exception as e:
                logger.error("Error downloading template from S3: %s, template will not be used", str(e))
                pass

        logger.info("Converting %s %s using %s", input_bucket, key, input_file.name)

        args = []
        if has_template:
            args = [
                "/opt/bin/pandoc",
                "--reference-doc",
                template_file.name,
                input_file.name,
                "-o",
                output_file.name,
            ]
        else:
            args = ["/opt/bin/pandoc", input_file.name, "-o", output_file.name]

        check_output(args)

        logger.info("Converted %s %s to %s", input_bucket, key, output_file.name)

        output_key = f"conversion/output/{email}/{uuid}.{fmat}"
        
        logger.debug("Handler parsed email: '%s' from key: '%s'", email, key)
        logger.debug("Handler saving file to output key: '%s'", output_key)
        logger.info("Uploading %s %s using %s", consolidation_bucket_name, output_key, output_file.name)

        upload_file_to_s3(consolidation_bucket_name, output_key, output_file.name, mime_type)

        logger.info("Uploaded %s %s using %s", consolidation_bucket_name, output_key, output_file.name)

        input_file.close()
        output_file.close()

    return
