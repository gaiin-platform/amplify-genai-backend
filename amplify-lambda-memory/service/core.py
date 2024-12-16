import json
import boto3
import os
from boto3.dynamodb.conditions import Key
from datetime import datetime
import uuid
from common.validate import validated
import requests

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
memory_table = dynamodb.Table(os.environ["MEMORY_DYNAMO_TABLE"])


# helper function to call LLM to extract facts/memories from user's prompt
def prompt_llm(prompt, access_token):
    # URL for the Amplify API
    url = "https://dev-api.vanderbilt.ai/chat" # TODO: replace with prod endpoint

    # Headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    message = [
        {
            "role": "user",
            "content": f"{prompt}",
        }
    ]

    # Data payload
    payload = {
        "data": {
            "model": "gpt-4o",
            "temperature": 0,
            "max_tokens": 4096,
            "dataSources": [],
            "messages": message,
            "options": {
                "ragOnly": False,
                "skipRag": True,
                "model": {"id": "gpt-4o"},
                "prompt": message[0]["content"] if message else "",
            },
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        response_data = response.json()
        txt = response_data.get("data", "")
        print(txt)
        return txt
    except:
        print(f"An error occurred while prompting the LLM")
        return None


@validated("extract_facts")
def extract_facts(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        access_token = data["access_token"]

        # Extract user's input in the conversation from payload
        user_input = nested_data["user_input"]

        # extract facts from conversation
        prompt = f"""Analyze the given text and identify key factual information. Extract and list the most important facts, focusing on specific details, names, dates, locations, or any other concrete information that could be relevant for future reference. Avoid opinions or interpretations.

        Present the extracted facts in the following format:
        FACT: [Extracted fact]
        FACT: [Extracted fact]
        FACT: [Extracted fact]

        Here is the text to extract facts from:
        {user_input}"""

        print("Prompt passed:", prompt)
        response = prompt_llm(prompt, access_token)

        extracted_facts = []
        for line in response.split("\n"):
            if line.startswith("FACT:"):
                extracted_facts.append(line[5:].strip())

        return {"statusCode": 200, "body": json.dumps({"facts": extracted_facts})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


# TODO: add pagination to enable reading large number of memories
@validated("read_memory")
def read_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        assistant_id = nested_data.get("assistant_id")

        # Use the GSI to query records for the current user
        response = memory_table.query(
            IndexName="UserIndex", KeyConditionExpression=Key("user").eq(current_user)
        )

        items = response.get("Items", [])

        if assistant_id:
            # Include user memories AND assistant memories
            items = [
                item
                for item in items
                if (
                    item.get("memory_type") == "user"
                    or (
                        item.get("memory_type") == "assistant"
                        and item.get("memory_type_id") == assistant_id
                    )
                )
            ]
        else:
            # Include ONLY user memories
            items = [item for item in items if item.get("memory_type") == "user"]

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

        valid_types = ["user", "assistant"]  # "group", "project", "conversation",
        if memory_type not in valid_types:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid memory type"}),
            }

        current_time = datetime.now()
        timestamp = current_time.isoformat()

        item_to_save = {
            "id": str(uuid.uuid4()),
            "memory_type": memory_type,
            "memory_type_id": memory_type_id,
            "content": memory_item,
            "user": current_user,
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


@validated("remove_memory")
def remove_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        memory_id = nested_data["memory_id"]

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
