import json
import boto3
import os
from boto3.dynamodb.conditions import Key
from datetime import datetime
import uuid
from common.validate import validated

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
memory_table = dynamodb.Table(os.environ["MEMORY_DYNAMO_TABLE"])


def extract_facts(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        # Extract request details
        conversation = nested_data["conversation"]

        # TODO: Implement logic to extract facts from conversation

        extracted_facts = ["Fact 1", "Fact 2", "Fact 3"]  # Placeholder

        return {"statusCode": 200, "body": json.dumps({"facts": extracted_facts})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def read_memory(event, context, current_user, name, data):
    try:
        # Parse the incoming event body
        body = json.loads(event["body"])
        query = body.get("query")

        # TODO: Implement logic to query the memory table
        # This is a simplified example
        response = memory_table.query(KeyConditionExpression=Key("id").eq(query))

        items = response.get("Items", [])

        return {"statusCode": 200, "body": json.dumps({"memories": items})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("save_memory")
def save_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        memory_item = nested_data["MemoryItem"]
        memory_type = nested_data["MemoryType"]
        memory_type_id = nested_data["MemoryTypeID"]

        valid_types = ["user", "group", "project", "conversation", "assistant"]
        if memory_type not in valid_types:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid memory type"}),
            }

        current_time = datetime.now()
        date = current_time.strftime("%Y-%m-%d")
        timestamp = current_time.isoformat()

        item_to_save = {
            "id": str(uuid.uuid4()),
            "memory_type": memory_type,
            "memory_type_id": memory_type_id,
            "content": memory_item,
            "created_by": current_user,
            "date": date,
            "timestamp": timestamp,
        }

        response = memory_table.put_item(Item=item_to_save)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Memory saved successfully", "id": item_to_save["id"]}
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def remove_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        memory_id = nested_data["id"]

        if not memory_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Memory ID is required"}),
            }

        # Delete the item from the DynamoDB table
        response = memory_table.delete_item(Key={"id": memory_id})

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Memory deleted successfully"}),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
