import os
import boto3
import uuid
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
import json

def register_workflow_template(current_user, template, name, description, input_schema, output_schema):
    # Get environment variables
    table_name = os.environ.get('WORKFLOW_TEMPLATES_TABLE')
    bucket_name = os.environ.get('WORKFLOW_TEMPLATES_BUCKET')

    if not table_name or not bucket_name:
        raise ValueError("Environment variables 'WORKFLOW_TEMPLATES_TABLE' and 'WORKFLOW_TEMPLATES_BUCKET' must be set.")

    # Initialize AWS clients
    dynamodb = boto3.client('dynamodb')
    s3 = boto3.client('s3')

    # Generate a unique UUID for the new workflow template
    template_id = str(uuid.uuid4())  # Changed from template_uuid to template_id
    s3_key = f"{current_user}/{template_id}.json"

    try:
        # Save the template to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(template),
            ContentType="application/json"
        )

        # Prepare the item to be inserted into the DynamoDB table using camel case
        serializer = TypeSerializer()
        item = {
            'user': serializer.serialize(current_user),
            'templateId': serializer.serialize(template_id),  # Use camel case
            's3Key': serializer.serialize(s3_key),  # Use camel case
            'name': serializer.serialize(name),
            'description': serializer.serialize(description),
            'inputSchema': serializer.serialize(input_schema),  # Use camel case
            'outputSchema': serializer.serialize(output_schema),  # Use camel case
        }

        # Insert the metadata into the DynamoDB table
        dynamodb.put_item(
            TableName=table_name,
            Item=item
        )

        return template_id  # Changed from template_uuid to template_id
    except Exception as e:
        raise RuntimeError(f"Failed to register workflow template: {e}")

def get_workflow_template(current_user, template_id):  # Changed from template_uuid to template_id
    # Get environment variables
    table_name = os.environ.get('WORKFLOW_TEMPLATES_TABLE')
    bucket_name = os.environ.get('WORKFLOW_TEMPLATES_BUCKET')

    if not table_name or not bucket_name:
        raise ValueError("Environment variables 'WORKFLOW_TEMPLATES_TABLE' and 'WORKFLOW_TEMPLATES_BUCKET' must be set.")

    # Initialize AWS clients
    dynamodb = boto3.client('dynamodb')
    s3 = boto3.client('s3')

    try:
        # Lookup the workflow template in the DynamoDB table by hash = current_user, range = template_id
        response = dynamodb.get_item(
            TableName=table_name,
            Key={
                'user': {'S': current_user},
                'templateId': {'S': template_id}  # Use camel case
            }
        )

        # Check if the item exists
        if 'Item' not in response:
            return None

        # Deserialize the response item
        deserializer = TypeDeserializer()
        deserialized_item = {key: deserializer.deserialize(value) for key, value in response['Item'].items()}

        # Fetch the template from S3 using the s3Key
        s3_key = deserialized_item['s3Key']  # Use camel case
        s3_response = s3.get_object(Bucket=bucket_name, Key=s3_key)
        template = json.loads(s3_response['Body'].read().decode('utf-8'))

        # Combine metadata and template into a single object using camel case
        result = {
            'name': deserialized_item['name'],
            'description': deserialized_item['description'],
            'inputSchema': deserialized_item['inputSchema'],  # Use camel case
            'outputSchema': deserialized_item['outputSchema'],  # Use camel case
            'templateId': template_id,  # Changed to templateId
            'template': template
        }

        # Remove s3Key
        result.pop('s3Key', None)

        return result

    except Exception as e:
        raise RuntimeError(f"Failed to fetch workflow template: {e}")

def list_workflow_templates(current_user):
    # Get the table name from the environment variable
    table_name = os.environ.get('WORKFLOW_TEMPLATES_TABLE')
    if not table_name:
        raise ValueError("Environment variable 'WORKFLOW_TEMPLATES_TABLE' must be set.")

    # Initialize the DynamoDB client
    dynamodb = boto3.client('dynamodb')

    try:
        # Define expression attribute names to avoid reserved keyword issue
        expression_attribute_names = {
            "#user": "user"
        }

        # Query the DynamoDB table for all templates by the current user
        response = dynamodb.query(
            TableName=table_name,
            KeyConditionExpression="#user = :user",
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues={
                ":user": {"S": current_user}
            }
        )

        # Check if any items exist
        if 'Items' not in response or not response['Items']:
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
            }
            for item in response['Items']
        ]

        return templates

    except Exception as e:
        raise RuntimeError(f"Failed to list workflow templates: {e}")