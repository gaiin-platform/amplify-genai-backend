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
projects_table = dynamodb.Table(os.environ["PROJECTS_DYNAMO_TABLE"])


# helper function to call LLM to extract facts/memories from user's prompt
def prompt_llm(prompt, access_token):
    # URL for the Amplify API
    url = "https://dev-api.vanderbilt.ai/chat"  # TODO: replace with prod endpoint

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
        # print("Access Token:", access_token)

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

        # print("Prompt passed:", prompt)
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
        project_id = nested_data.get("project_id")  # Add this line

        # Use the GSI to query records for the current user
        response = memory_table.query(
            IndexName="UserIndex", KeyConditionExpression=Key("user").eq(current_user)
        )

        items = response.get("Items", [])

        # Filter based on memory types and IDs
        filtered_items = []
        for item in items:
            memory_type = item.get("memory_type")
            if (
                memory_type == "user"
                or (
                    memory_type == "assistant"
                    and assistant_id
                    and item.get("memory_type_id") == assistant_id
                )
                or (
                    memory_type == "project"
                    and project_id
                    and item.get("memory_type_id") == project_id
                )
                or (
                    memory_type == "project" and not project_id
                )  # Include all project memories if no specific project_id
            ):
                filtered_items.append(item)

        return {"statusCode": 200, "body": json.dumps({"memories": filtered_items})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("save_memory")
def save_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        memory_item = nested_data["MemoryItem"]
        memory_type = nested_data["MemoryType"]
        memory_type_id = nested_data["MemoryTypeID"]

        valid_types = ["user", "assistant", "project"]
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


@validated("edit_memory")
def edit_memory(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        memory_id = nested_data["memory_id"]
        new_content = nested_data["content"]

        # First verify the memory exists and belongs to the user
        memory = memory_table.get_item(Key={"id": memory_id}).get("Item")

        if not memory:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Memory not found"}),
            }

        if memory["user"] != current_user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Unauthorized to edit this memory"}),
            }

        # Update the memory content and timestamp
        current_time = datetime.now()
        timestamp = current_time.isoformat()

        response = memory_table.update_item(
            Key={"id": memory_id},
            UpdateExpression="SET content = :content, #ts = :timestamp",
            ExpressionAttributeValues={
                ":content": new_content,
                ":timestamp": timestamp,
            },
            ExpressionAttributeNames={"#ts": "timestamp"},
            ReturnValues="ALL_NEW",
        )

        updated_item = response.get("Attributes", {})

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Memory updated successfully", "memory": updated_item}
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
            "body": json.dumps({"message": "Memory removed successfully"}),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("create_project")
def create_project(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        project_name = nested_data["ProjectName"]

        # Generate unique project ID
        project_id = str(uuid.uuid4())

        current_time = datetime.now()
        timestamp = current_time.isoformat()

        project_item = {
            "id": project_id,
            "project": project_name,
            "user": current_user,
            "timestamp": timestamp,
        }

        response = projects_table.put_item(Item=project_item)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Project created successfully",
                    "id": project_id,
                    "project": project_name,
                }
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("get_projects")
def get_projects(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        email = nested_data["Email"]

        response = projects_table.query(
            IndexName="UserIndex", KeyConditionExpression=Key("user").eq(email)
        )

        projects = response.get("Items", [])
        print("Projects:", projects)

        return {
            "statusCode": 200,
            "body": json.dumps({"projects": projects}),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("remove_project")
def remove_project(event, context, current_user, name, data):
    try:
        nested_data = data["data"]

        project_id = nested_data["ProjectID"]

        # First verify the user owns this project
        project = projects_table.get_item(Key={"id": project_id}).get("Item")

        if not project:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Project not found"}),
            }

        if project["user"] != current_user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Unauthorized to remove this project"}),
            }

        # Delete the project
        response = projects_table.delete_item(Key={"id": project_id})

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Project removed successfully", "id": project_id}
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("edit_project")
def edit_project(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        project_id = nested_data["ProjectID"]
        new_name = nested_data["ProjectName"]

        # First verify the project exists and belongs to the user
        project = projects_table.get_item(Key={"id": project_id}).get("Item")

        if not project:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Project not found"}),
            }

        if project["user"] != current_user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Unauthorized to edit this project"}),
            }

        current_time = datetime.now()
        timestamp = current_time.isoformat()

        response = projects_table.update_item(
            Key={"id": project_id},
            UpdateExpression="SET #proj = :project_name, #ts = :timestamp",
            ExpressionAttributeValues={
                ":project_name": new_name,
                ":timestamp": timestamp,
            },
            ExpressionAttributeNames={"#proj": "project", "#ts": "timestamp"},
            ReturnValues="ALL_NEW",
        )

        updated_item = response.get("Attributes", {})

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Project updated successfully", "project": updated_item}
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
