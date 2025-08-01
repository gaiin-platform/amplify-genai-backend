import os
import boto3
import uuid
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
import json


def register_workflow_template(
    current_user, template, name, description, input_schema, output_schema
):
    # Get environment variables
    table_name = os.environ.get("WORKFLOW_TEMPLATES_TABLE")
    bucket_name = os.environ.get("WORKFLOW_TEMPLATES_BUCKET")

    if not table_name or not bucket_name:
        raise ValueError(
            "Environment variables 'WORKFLOW_TEMPLATES_TABLE' and 'WORKFLOW_TEMPLATES_BUCKET' must be set."
        )

    # Initialize AWS clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    # Generate a unique UUID for the new workflow template
    template_uuid = str(uuid.uuid4())
    s3_key = f"{current_user}/{template_uuid}.json"

    try:
        # Save the template to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(template),
            ContentType="application/json",
        )

        # Prepare the item to be inserted into the DynamoDB table
        serializer = TypeSerializer()
        item = {
            "user": serializer.serialize(current_user),
            "template_uuid": serializer.serialize(template_uuid),
            "s3_key": serializer.serialize(s3_key),
            "name": serializer.serialize(name),
            "description": serializer.serialize(description),
            "input_schema": serializer.serialize(input_schema),
            "output_schema": serializer.serialize(output_schema),
        }

        # Insert the metadata into the DynamoDB table
        dynamodb.put_item(TableName=table_name, Item=item)

        return template_uuid
    except Exception as e:
        raise RuntimeError(f"Failed to register workflow template: {e}")


def get_workflow_template(current_user, template_uuid):
    # Get environment variables
    table_name = os.environ.get("WORKFLOW_TEMPLATES_TABLE")
    bucket_name = os.environ.get("WORKFLOW_TEMPLATES_BUCKET")

    if not table_name or not bucket_name:
        raise ValueError(
            "Environment variables 'WORKFLOW_TEMPLATES_TABLE' and 'WORKFLOW_TEMPLATES_BUCKET' must be set."
        )

    # Initialize AWS clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    try:
        # Lookup the workflow template in the DynamoDB table by hash = current_user, range = template_uuid
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "template_uuid": {"S": template_uuid}},
        )

        # Check if the item exists
        if "Item" not in response:
            return None

        # Deserialize the response item
        deserializer = TypeDeserializer()
        deserialized_item = {
            key: deserializer.deserialize(value)
            for key, value in response["Item"].items()
        }

        # Fetch the template from S3 using the s3_key
        s3_key = deserialized_item["s3_key"]
        s3_response = s3.get_object(Bucket=bucket_name, Key=s3_key)
        template = json.loads(s3_response["Body"].read().decode("utf-8"))

        # Combine metadata and template into a single object
        result = {
            "name": deserialized_item["name"],
            "description": deserialized_item["description"],
            "input_schema": deserialized_item["input_schema"],
            "output_schema": deserialized_item["output_schema"],
            "template_uuid": template_uuid,
            "template": template,
        }

        return result

    except Exception as e:
        raise RuntimeError(f"Failed to fetch workflow template: {e}")


def list_workflow_templates(current_user):
    # Get the table name from the environment variable
    table_name = os.environ.get("WORKFLOW_TEMPLATES_TABLE")
    if not table_name:
        raise ValueError("Environment variable 'WORKFLOW_TEMPLATES_TABLE' must be set.")

    # Initialize the DynamoDB client
    dynamodb = boto3.client("dynamodb")

    try:
        # Query the DynamoDB table for all templates by the current user
        response = dynamodb.query(
            TableName=table_name,
            KeyConditionExpression="user = :user",
            ExpressionAttributeValues={":user": {"S": current_user}},
        )

        # Check if any items exist
        if "Items" not in response or not response["Items"]:
            return []

        # Deserialize the items into a list of metadata dictionaries
        deserializer = TypeDeserializer()
        templates = [
            {
                "template_uuid": deserializer.deserialize(item["template_uuid"]),
                "name": deserializer.deserialize(item["name"]),
                "description": deserializer.deserialize(item["description"]),
                "input_schema": deserializer.deserialize(item["input_schema"]),
                "output_schema": deserializer.deserialize(item["output_schema"]),
                "s3_key": deserializer.deserialize(item["s3_key"]),
            }
            for item in response["Items"]
        ]

        return templates

    except Exception as e:
        raise RuntimeError(f"Failed to list workflow templates: {e}")
