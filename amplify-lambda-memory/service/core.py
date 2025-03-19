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

# store this as JSON in s3
TAXONOMY_STRUCTURE = {
    "Identity": ["Role", "Department", "Area_of_Study", "Preferences"],
    "Projects": [
        "Name",
        "Type",
        "Status",
        "Timeline",
        "Requirements",
        "Resources",
        "Collaborators",
    ],
    "Academic_Content": [
        "Research",
        "Teaching_Materials",
        "Publications",
        "Presentations",
        "Data_Analysis",
        "Code",
        "Visual_Assets",
    ],
    "Relationships": ["Collaborators", "Advisors_Advisees", "Teams", "Committees"],
    "Time_Sensitive": ["Deadlines", "Meetings", "Events", "Academic_Calendar"],
    "Resources": ["Tools", "Datasets", "References", "Documentation", "Templates"],
    "Knowledge": [
        "Subject_Matter",
        "Methods",
        "Procedures",
        "Best_Practices",
        "Institutional_Policies",
    ],
}


# helper function for extract facts
def get_current_taxonomy_state(current_user):
    """Retrieve all memories and organize them by taxonomy"""
    response = memory_table.query(
        IndexName="UserIndex", KeyConditionExpression=Key("user").eq(current_user)
    )

    taxonomy_state = {
        category: {subcategory: [] for subcategory in subcategories}
        for category, subcategories in TAXONOMY_STRUCTURE.items()
    }

    for item in response.get("Items", []):
        if "taxonomy_path" in item:
            category, subcategory = item["taxonomy_path"].split("/")
            if category in taxonomy_state and subcategory in taxonomy_state[category]:
                taxonomy_state[category][subcategory].append(item["content"])

    return taxonomy_state


# helper function for extract facts
def format_taxonomy_state(taxonomy_state):
    """Format the taxonomy state into a string for the prompt"""
    formatted = "Current Knowledge Base:\n\n"
    for category, subcategories in taxonomy_state.items():
        formatted += f"{category}/\n"
        for subcategory, memories in subcategories.items():
            formatted += f"  {subcategory}/\n"
            for memory in memories:
                formatted += f"    - {memory}\n"
    return formatted


def validate_taxonomy_path(taxonomy_path, taxonomy_structure):
    """
    Validate that a taxonomy path exists in the taxonomy structure

    Args:
        taxonomy_path (str): Path in format "Category/Subcategory"
        taxonomy_structure (dict): The taxonomy structure dictionary

    Returns:
        bool: True if valid, False if invalid
    """
    try:
        category, subcategory = taxonomy_path.split("/")
        return (
            category in taxonomy_structure
            and subcategory in taxonomy_structure[category]
        )
    except:
        return False


def save_memory_with_taxonomy(
    memory_table,
    user,
    content,
    taxonomy_path,
    memory_type="user",
    memory_type_id=None,
    conversation_id=None,
):
    """
    Save a memory with its taxonomy classification

    Args:
        memory_table: DynamoDB table object
        user (str): Current user identifier
        content (str): The memory content
        taxonomy_path (str): Path in format "Category/Subcategory"
        memory_type (str): Type of memory (user, assistant, or project)
        memory_type_id (str): ID of the assistant or project if applicable
        conversation_id (str): ID of the conversation this memory came from

    Returns:
        dict: The saved item
    """
    current_time = datetime.now()
    timestamp = current_time.isoformat()

    item_to_save = {
        "id": str(uuid.uuid4()),
        "memory_type": memory_type,
        "memory_type_id": memory_type_id,
        "content": content,
        "taxonomy_path": taxonomy_path,
        "user": user,
        "timestamp": timestamp,
        "conversation_id": conversation_id,
    }

    memory_table.put_item(Item=item_to_save)
    return item_to_save


@validated("save_memory_batch")
def save_memory_batch(event, context, current_user, name, data):
    """
    Save multiple memories with their taxonomy classifications

    Expected data format:
    {
        "memories": [
            {
                "content": "memory content",
                "taxonomy_path": "Category/Subcategory",
                "memory_type": "user",
                "memory_type_id": null,
                "conversation_id": "conversation-uuid"
            },
            ...
        ]
    }
    """
    try:
        nested_data = data["data"]
        memories = nested_data["memories"]

        saved_items = []
        failed_items = []

        for memory in memories:
            try:
                if not validate_taxonomy_path(
                    memory["taxonomy_path"], TAXONOMY_STRUCTURE
                ):
                    failed_items.append(
                        {"content": memory["content"], "error": "Invalid taxonomy path"}
                    )
                    continue

                item = save_memory_with_taxonomy(
                    memory_table=memory_table,
                    user=current_user,
                    content=memory["content"],
                    taxonomy_path=memory["taxonomy_path"],
                    memory_type=memory.get("memory_type", "user"),
                    memory_type_id=memory.get("memory_type_id"),
                    conversation_id=memory.get("conversation_id"),
                )
                saved_items.append(item)

            except Exception as e:
                failed_items.append({"content": memory["content"], "error": str(e)})

        return {
            "statusCode": 200,
            "body": json.dumps({"saved": saved_items, "failed": failed_items}),
        }

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("read_memory_by_taxonomy")
def read_memory_by_taxonomy(event, context, current_user, name, data):
    """
    Read memories filtered by taxonomy path

    Expected data format:
    {
        "category": "Category",  // optional
        "subcategory": "Subcategory",  // optional
        "memory_type": "user",  // optional
        "memory_type_id": "id",  // optional
        "conversation_id": "conversation-uuid"  // optional
    }
    """
    try:
        nested_data = data["data"]
        category = nested_data.get("category")
        subcategory = nested_data.get("subcategory")
        memory_type = nested_data.get("memory_type")
        memory_type_id = nested_data.get("memory_type_id")
        conversation_id = nested_data.get("conversation_id")

        # Query by user first
        response = memory_table.query(
            IndexName="UserIndex", KeyConditionExpression=Key("user").eq(current_user)
        )

        items = response.get("Items", [])

        # Apply filters
        filtered_items = []
        for item in items:
            if "taxonomy_path" not in item:
                continue

            item_category, item_subcategory = item["taxonomy_path"].split("/")

            # Filter by category if specified
            if category and item_category != category:
                continue

            # Filter by subcategory if specified
            if subcategory and item_subcategory != subcategory:
                continue

            # Filter by memory type if specified
            if memory_type and item.get("memory_type") != memory_type:
                continue

            # Filter by memory type ID if specified
            if memory_type_id and item.get("memory_type_id") != memory_type_id:
                continue

            # Filter by conversation ID if specified
            if conversation_id and item.get("conversation_id") != conversation_id:
                continue

            filtered_items.append(item)

        return {"statusCode": 200, "body": json.dumps({"memories": filtered_items})}

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


@validated("update_memory_taxonomy")
def update_memory_taxonomy(event, context, current_user, name, data):
    """
    Update the taxonomy classification of an existing memory

    Expected data format:
    {
        "memory_id": "id",
        "new_taxonomy_path": "Category/Subcategory"
    }
    """
    try:
        nested_data = data["data"]
        memory_id = nested_data["memory_id"]
        new_taxonomy_path = nested_data["new_taxonomy_path"]

        # Validate new taxonomy path
        if not validate_taxonomy_path(new_taxonomy_path, TAXONOMY_STRUCTURE):
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid taxonomy path"}),
            }

        # Verify memory exists and belongs to user
        memory = memory_table.get_item(Key={"id": memory_id}).get("Item")

        if not memory:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Memory not found"}),
            }

        if memory["user"] != current_user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Unauthorized to update this memory"}),
            }

        # Update taxonomy path
        response = memory_table.update_item(
            Key={"id": memory_id},
            UpdateExpression="SET taxonomy_path = :path",
            ExpressionAttributeValues={":path": new_taxonomy_path},
            ReturnValues="ALL_NEW",
        )

        updated_item = response.get("Attributes", {})

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Taxonomy path updated successfully",
                    "memory": updated_item,
                }
            ),
        }

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


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


# TODO: give the model an out! if the fact does not fit into the taxonomy well, it needs to have the ability to place it outside the taxonomy
@validated("extract_facts")
def extract_facts(event, context, current_user, name, data):
    try:
        nested_data = data["data"]
        access_token = data["access_token"]
        # print("Access Token:", access_token)

        # Extract user's input in the conversation from payload
        user_input = nested_data["user_input"]

        taxonomy_state = get_current_taxonomy_state(current_user)
        formatted_taxonomy = format_taxonomy_state(taxonomy_state)

        # extract facts from conversation
        #         prompt = f"""Extract facts from the text, preserving any personal perspectives exactly as stated. Each fact must be:
        # - Written exactly as presented (keep "I", "my", "we" if present)
        # - Include specific details when present
        # - Free of opinions or interpretations

        # Present the extracted facts in the following format:
        # FACT: [Extracted fact]
        # FACT: [Extracted fact]
        # FACT: [Extracted fact]

        # Here is the text to extract facts from:
        # {user_input}"""
        # extract facts into taxonomy structure

        # generate path from root to node
        # first look where it goes, then look at facts at and below that lead

        prompt = f"""Given the following taxonomy structure and current state of knowledge, analyze the new text and extract novel facts that don't duplicate existing information. For each fact, determine its appropriate place in the taxonomy. Preserve any personal perspectives exactly as stated. Each fact must be:
- Written exactly as presented (keep "I", "my", "we" if present)
- Include specific details when present
- Free of opinions or interpretations

Taxonomy Structure:
{json.dumps(TAXONOMY_STRUCTURE, indent=2)}

{formatted_taxonomy}

For each extracted fact, provide both the fact and its classification in the following format:
FACT: [Extracted fact]
TAXONOMY: [Category]/[Subcategory]
REASONING: [Brief explanation of why this fact belongs in this category]

Only extract facts that add new information not already present in the current knowledge base.

Text to analyze:
{user_input}"""

        # print("Prompt passed:", prompt)
        response = prompt_llm(prompt, access_token)

        facts = []
        current_fact = {}

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("FACT:"):
                if current_fact:
                    facts.append(current_fact)
                current_fact = {"content": line[5:].strip()}
            elif line.startswith("TAXONOMY:"):
                current_fact["taxonomy_path"] = line[9:].strip()
            elif line.startswith("REASONING:"):
                current_fact["reasoning"] = line[10:].strip()

        if current_fact:
            facts.append(current_fact)

        return {"statusCode": 200, "body": json.dumps({"facts": facts})}
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
