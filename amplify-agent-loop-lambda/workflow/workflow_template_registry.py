from datetime import datetime
import os
import boto3
import uuid
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
import json


def register_workflow_template(
    current_user,
    template,
    name,
    description,
    input_schema,
    output_schema,
    is_base_template,
    is_public,
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
    template_id = str(uuid.uuid4())  # Changed from template_uuid to template_id
    s3_key = f"{current_user}/{template_id}.json"
    print("registering workflow template: ", template_id)
    try:
        # Save the template to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(template),
            ContentType="application/json",
        )

        # Prepare the item to be inserted into the DynamoDB table using camel case
        serializer = TypeSerializer()
        item = {
            "user": serializer.serialize(current_user),
            "templateId": serializer.serialize(template_id),  # Use camel case
            "isBaseTemplate": serializer.serialize(is_base_template),
            "isPublic": {"N": "1" if is_public else "0"},
            "s3Key": serializer.serialize(s3_key),  # Use camel case
            "name": serializer.serialize(name),
            "description": serializer.serialize(description),
            "inputSchema": serializer.serialize(input_schema),  # Use camel case
            "outputSchema": serializer.serialize(output_schema),  # Use camel case
            "createdAt": serializer.serialize(datetime.now().isoformat()),
        }

        # Insert the metadata into the DynamoDB table
        dynamodb.put_item(TableName=table_name, Item=item)

        return template_id  # Changed from template_uuid to template_id
    except Exception as e:
        print(f"Error registering workflow template: {e}")
        raise RuntimeError(f"Failed to register workflow template: {e}")


def get_workflow_template(
    current_user, template_id
):  # Changed from template_uuid to template_id
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
        # Lookup the workflow template in the DynamoDB table by hash = current_user, range = template_id
        response = dynamodb.get_item(
            TableName=table_name,
            Key={
                "user": {"S": current_user},
                "templateId": {"S": template_id},  # Use camel case
            },
        )

        # Check if the item exists
        if "Item" not in response:
            is_public_response = dynamodb.query(
                TableName=table_name,
                IndexName="TemplateIdPublicIndex",
                KeyConditionExpression="templateId = :tid AND isPublic = :pub",
                ExpressionAttributeValues={
                    ":tid": {"S": template_id},
                    ":pub": {"N": "1"},
                },
            )

            if "Items" in is_public_response and is_public_response["Items"]:
                response = {"Item": is_public_response["Items"][0]}
            else:
                print(f"No public template found for template_id: {template_id}")
                return None

        # Deserialize the response item
        deserializer = TypeDeserializer()
        deserialized_item = {
            key: deserializer.deserialize(value)
            for key, value in response["Item"].items()
        }

        # Fetch the template from S3 using the s3Key
        s3_key = deserialized_item["s3Key"]  # Use camel case
        s3_response = s3.get_object(Bucket=bucket_name, Key=s3_key)
        template = json.loads(s3_response["Body"].read().decode("utf-8"))

        # Combine metadata and template into a single object using camel case
        result = {
            "name": deserialized_item["name"],
            "description": deserialized_item["description"],
            "inputSchema": deserialized_item["inputSchema"],  # Use camel case
            "outputSchema": deserialized_item["outputSchema"],  # Use camel case
            "templateId": template_id,  # Changed to templateId
            "template": template,
            "isPublic": True if deserialized_item.get("isPublic") == 1 else False,
            "isBaseTemplate": deserialized_item.get("isBaseTemplate", False),
        }

        # Remove s3Key
        result.pop("s3Key", None)

        return result

    except Exception as e:
        raise RuntimeError(f"Failed to fetch workflow template: {e}")

def list_workflow_templates(current_user, include_public_templates=False):
    # Get the table name from the environment variable
    table_name = os.environ.get("WORKFLOW_TEMPLATES_TABLE")
    if not table_name:
        raise ValueError("Environment variable 'WORKFLOW_TEMPLATES_TABLE' must be set.")

    # Initialize the DynamoDB client
    dynamodb = boto3.client("dynamodb")

    try:
        # Define expression attribute names to avoid reserved keyword issue
        expression_attribute_names = {"#user": "user"}

        # Query the DynamoDB table for all templates by the current user
        user_response = dynamodb.query(
            TableName=table_name,
            KeyConditionExpression="#user = :user",
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues={":user": {"S": current_user}},
        )

        # Start with user's templates
        all_items = []
        if 'Items' in user_response and user_response['Items']:
            all_items.extend(user_response['Items'])

        # Only scan for public templates if flag is True
        if include_public_templates:
            # Scan for public templates from other users (exclude current user)
            public_response = dynamodb.scan(
                TableName=table_name,
                FilterExpression="isPublic = :pub AND #user <> :current_user",
                ExpressionAttributeNames={
                    "#user": "user"
                },
                ExpressionAttributeValues={
                    ":pub": {"N": "1"},
                    ":current_user": {"S": current_user}
                }
            )

            if 'Items' in public_response and public_response['Items']:
                all_items.extend(public_response['Items'])

        # Check if any items exist
        if not all_items:
            return []

        # Deserialize the items into a list of metadata dictionaries
        deserializer = TypeDeserializer()
        templates = [
            {
                'templateId': deserializer.deserialize(item['templateId']),  # Use camel case
                'name': deserializer.deserialize(item['name']),
                'description': deserializer.deserialize(item['description']),
                'inputSchema': deserializer.deserialize(item['inputSchema']),  # Use camel case
                'outputSchema': deserializer.deserialize(item['outputSchema']),  # Use camel case
                'isBaseTemplate': deserializer.deserialize(item['isBaseTemplate']) if 'isBaseTemplate' in item else False,
                'isPublic': bool(deserializer.deserialize(item['isPublic'])) if 'isPublic' in item else False,
                'user': deserializer.deserialize(item['user']),  # Include user info to distinguish ownership
            }
            for item in all_items
        ]

        return templates

    except Exception as e:
        print(f"Error listing workflow templates: {e}")
        raise RuntimeError(f"Failed to list workflow templates: {e}")


def delete_workflow_template(current_user, template_id):
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
        # First, check if the template exists and belongs to the user
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "templateId": {"S": template_id}},
        )

        # If template doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return {
                "success": False,
                "message": "Template not found or you don't have permission to delete it",
            }

        # Get the S3 key before deleting the DynamoDB record
        s3_key = TypeDeserializer().deserialize(response["Item"]["s3Key"])

        # Delete the template from DynamoDB
        dynamodb.delete_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "templateId": {"S": template_id}},
        )

        # Delete the template from S3
        s3.delete_object(Bucket=bucket_name, Key=s3_key)

        return {
            "success": True,
            "message": f"Template {template_id} deleted successfully",
        }

    except Exception as e:
        print(f"Error deleting workflow template: {e}")
        raise RuntimeError(f"Failed to delete workflow template: {e}")


def update_workflow_template(
    current_user,
    template_id,
    template,
    name,
    description,
    input_schema,
    output_schema,
    is_base_template,
    is_public,
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

    try:
        # First, check if the template exists and belongs to the user
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "templateId": {"S": template_id}},
        )

        # If template doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return {
                "success": False,
                "message": "Template not found or you don't have permission to update it",
            }

        # Get the existing S3 key
        s3_key = TypeDeserializer().deserialize(response["Item"]["s3Key"])

        # Update the template in S3
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(template),
            ContentType="application/json",
        )

        # Prepare the updated item for DynamoDB using camel case
        serializer = TypeSerializer()
        updated_item = {
            "user": serializer.serialize(current_user),
            "templateId": serializer.serialize(template_id),
            "isBaseTemplate": serializer.serialize(is_base_template),
            "isPublic": {"N": "1" if is_public else "0"},
            "s3Key": serializer.serialize(s3_key),
            "name": serializer.serialize(name),
            "description": serializer.serialize(description),
            "inputSchema": serializer.serialize(input_schema),
            "outputSchema": serializer.serialize(output_schema),
            "updatedAt": serializer.serialize(datetime.now().isoformat()),
        }

        # Preserve the original creation timestamp if it exists
        if "createdAt" in response["Item"]:
            updated_item["createdAt"] = response["Item"]["createdAt"]
        else:
            updated_item["createdAt"] = serializer.serialize(datetime.now().isoformat())

        # Update the item in DynamoDB
        dynamodb.put_item(TableName=table_name, Item=updated_item)

        return {
            "success": True,
            "message": f"Template {template_id} updated successfully",
            "templateId": template_id,
        }

    except Exception as e:
        print(f"Error updating workflow template: {e}")
        raise RuntimeError(f"Failed to update workflow template: {e}")
