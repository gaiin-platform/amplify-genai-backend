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

setup_validated(rules, get_permission_checker)

# AWS S3 Client
s3 = boto3.client("s3")


def download_file_from_s3(bucket, key, file_path):
    try:
        s3.download_file(bucket, key, file_path)
        print("Downloaded", bucket, key)
        return file_path
    except NoCredentialsError:
        print("Credentials not available")
        return None


def upload_file_to_s3(bucket, key, file_path, content_type):
    try:
        s3.upload_file(
            file_path,
            bucket,
            key,
            ExtraArgs={"ACL": "private", "ContentType": content_type},
        )
        print("Uploaded", bucket, key)
    except NoCredentialsError:
        print("Credentials not available")


def parse_s3_key(s3_key):
    basename = s3_key.rsplit(".", 1)[0]
    extension = s3_key.rsplit(".", 1)[1]
    print(f"basename: {basename}, extension: {extension}")

    email = basename.split("/")[0]
    uuid_format = basename.split("/")[1]
    uuid = uuid_format.split("-to-")[0]
    fmat = uuid_format.split("-to-")[1]

    if email and uuid and fmat and extension:
        print(f"email: {email}, uuid: {uuid}, format: {fmat}, extension: {extension}")
        return email, uuid, fmat, extension
    else:
        return None, None, None, None


@validated("convert")
def submit_conversion_job(event, context, user, name, data):
    print(f"User {user} submitted conversion job")
    input_bucket_name = os.environ["S3_CONVERSION_INPUT_BUCKET_NAME"]
    output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

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

    # Construct the key
    s3_key = f"{user}/{unique_uuid}-to-{fmat}.md"

    # Initialize the S3 client
    s3_client = boto3.client("s3")

    print(
        f"Saving file to S3 with key: {s3_key} and content type: text/markdown and bucket: {input_bucket_name}"
    )
    # Save the contents to the bucket
    s3_client.put_object(
        Body=contents_to_convert,
        Bucket=input_bucket_name,
        Key=s3_key,
        ContentType="text/markdown",
    )

    print(f"File saved to S3 with key: {s3_key}")

    if templateName and templateName != "":
        put_template_to_s3(user, unique_uuid, templateName)

    s3_output_key = f"{user}/{unique_uuid}.{fmat}"

    print(f"Returning presigned URL for key: {s3_output_key} and bucket: {output_bucket_name}")

    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": output_bucket_name, "Key": s3_output_key},
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
        output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

        s3_template_key = f"{user}/{unique_uuid}.template"
        print(
            f"Uploading template name '{template_name}' to S3 with key: {s3_template_key}"
        )
        content = f"{template_name}"

        # Put the content string into S3
        s3_client.put_object(
            Body=content,
            Bucket=output_bucket_name,
            Key=s3_template_key,
            ContentType="text/plain",
        )

        print(f"Template name uploaded to S3 with key: {s3_template_key}")
    except Exception as e:
        print(f"Error uploading template to S3: {str(e)}, template will not be used")
        pass


def get_template_from_s3(user: str, unique_uuid: str):
    # Construct the template key
    s3_template_key = f"{user}/{unique_uuid}.template"

    # Initialize the S3 client
    s3_client = boto3.client("s3")

    output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

    try:
        # Try to get the object from S3
        response = s3_client.get_object(Bucket=output_bucket_name, Key=s3_template_key)

        # Read the content from the body
        data = response["Body"].read()

        # Decode the bytes to a string
        content = data.decode("utf-8")

        print(f"Template fetched from S3 with key: {s3_template_key}")

        return content

    except s3_client.exceptions.NoSuchKey:
        print(f"No template found in S3 with key: {s3_template_key}")

    except Exception as e:
        print(f"Error fetching template from S3: {str(e)}")

    return None


def handler(event, context):

    input_bucket_name = os.environ["S3_CONVERSION_INPUT_BUCKET_NAME"]
    output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

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

        print("Processing document conversion for key", key)

        input_bucket = record["s3"]["bucket"]["name"]

        email, uuid, fmat, extension = parse_s3_key(key)

        print(f"bucket:{input_bucket} user: {email}, uuid: {uuid}, format: {fmat}, extension: {extension}")

        if not email or not uuid or not fmat or not extension:
            print("Could not parse email, uuid, format, and extension from key", key)
            return

        output_file = tempfile.NamedTemporaryFile(suffix="." + fmat, delete=False)

        mime_type = supported_mime_types.get(fmat, "text/plain")

        print(f"input_bucket: {input_bucket}, key: {key}, input_file: {input_file.name}, output_file: {output_file.name}, mime_type: {mime_type}")

        download_file_from_s3(input_bucket, key, input_file.name)

        template_name = get_template_from_s3(email, uuid)
        has_template = False

        if template_name and template_name != "":
            try:
                template_key = f"templates/{template_name}"
                suffix = template_key.rsplit(".", 1)[1]
                print(f"template_key: {template_key}, suffix: {suffix}")
                template_file = tempfile.NamedTemporaryFile(
                    suffix="." + suffix, delete=False
                )
                download_file_from_s3(
                    output_bucket_name, template_key, template_file.name
                )
                has_template = True
            except Exception as e:
                print(
                    f"Error downloading template from S3: {str(e)}, template will not be used"
                )
                pass

        print("converting", input_bucket, key, "using", input_file.name)

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

        print("converted", input_bucket, key, "to", output_file.name)

        output_key = email + "/" + uuid + "." + fmat

        print("uploading", output_bucket_name, output_key, "using", output_file.name)

        upload_file_to_s3(output_bucket_name, output_key, output_file.name, mime_type)

        print("uploaded", output_bucket_name, output_key, "using", output_file.name)

        input_file.close()
        output_file.close()

    return
