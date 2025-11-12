import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from delegation.api_keys import create_agent_event_api_key
from pycommon.api.api_key import deactivate_key
from pycommon.logger import getLogger
logger = getLogger("agent_event_templates")

# Initialize AWS resources
dynamodb = boto3.resource("dynamodb")

event_table = dynamodb.Table(os.getenv("AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE"))
assistant_alias_table = dynamodb.Table(os.getenv("ASSISTANTS_ALIASES_DYNAMODB_TABLE"))
assistant_table = dynamodb.Table(os.getenv("ASSISTANTS_DYNAMODB_TABLE"))

### ==========================
### 🚀 Assistant Resolution Functions
### ==========================


def get_assistant_by_alias(user, assistant_alias):
    """
    Retrieves an assistant using its alias for a given user.

    Returns:
        dict: {success, data, message}
    """
    try:
        alias_key = f"{assistant_alias}?type=latest"
        response = assistant_alias_table.get_item(
            Key={"user": user, "assistantId": alias_key}
        )

        if "Item" in response:
            assistant_data = response["Item"]
            assistant_id = assistant_data["data"]["id"]

            assistant_response = assistant_table.get_item(Key={"id": assistant_id})
            if "Item" in assistant_response:
                return {
                    "success": True,
                    "data": assistant_response["Item"],
                    "message": "Assistant found.",
                }

            return {
                "success": False,
                "message": f"The assistant linked to alias '{assistant_alias}' was not found. "
                f"Double-check your assistantId and ensure the assistant wasn't deleted.",
            }

        return {
            "success": False,
            "message": f"No assistant alias '{assistant_alias}' found for user '{user}'. "
            f"Verify the alias is correct and that the assistant exists.",
        }

    except ClientError as e:
        logger.error("Error retrieving assistant by alias: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to retrieve assistant at this time. Please try again later.",
        }


### ==========================
### 🚀 Event Template Functions
### ==========================


def add_event_template(
    user, access_token, tag, prompt, account, description, assistant_id=None
):
    """
    Creates an API key and adds its ID to an event template in DynamoDB.

    Args:
        user (str): The user creating the template.
        access_token (str): Authorization token.
        tag (str): Tag for the event template.
        prompt (str): The event prompt.
        account (str): The account associated with the API key.
        description (str): Description of the API key.
        assistant_id (str, optional): Assistant ID, if applicable.

    Returns:
        dict: {success, data, message}
    """
    try:
        # Verify assistant exists if provided
        if assistant_id:
            response = get_assistant_by_alias(user, assistant_id)
            if not response["success"]:
                return {
                    "success": False,
                    "message": f"Cannot add event template: {response['message']}",
                }

        # Step 1: Create an API Key for this event
        api_key_response = create_agent_event_api_key(
            user=user,
            token=access_token,
            agent_event_name=tag,
            account=account,
            description=description,
            purpose="email_event",
        )

        if not api_key_response or not api_key_response.get("success"):
            return {
                "success": False,
                "message": "Failed to create API key. Event template was not added.",
            }

        # Extract API Key ID
        api_key_id = api_key_response["data"]["id"]

        # Step 2: Construct the event template
        item = {
            "user": user,
            "tag": tag,
            "prompt": prompt,
            "apiKeyId": api_key_id,
        }

        if assistant_id:
            item["assistantId"] = assistant_id

        # Step 3: Save the event template to DynamoDB
        event_table.put_item(Item=item)

        return {
            "success": True,
            "message": f"Event template '{tag}' successfully added for user '{user}' with API Key ID '{api_key_id}'.",
        }

    except ClientError as e:
        logger.error("Error adding event template: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to add event template. Please try again later.",
        }


def remove_event_template(user, tag, access_token):
    """
    Removes an event template from DynamoDB.

    Returns:
        dict: {success, data, message}
    """
    try:

        response = event_table.get_item(Key={"user": user, "tag": tag})
        if "Item" not in response:
            return {
                "success": False,
                "message": f"No event template found for tag '{tag}' and user '{user}'. "
                f"Check if the tag is correct or if the template was already removed.",
            }

        event_template = response["Item"]

        deactivate_key(access_token, event_template["apiKeyId"])

        event_table.delete_item(Key={"user": user, "tag": tag})
        return {
            "success": True,
            "message": f"Event template '{tag}' removed successfully for user '{user}'.",
        }

    except ClientError as e:
        logger.error("Error removing event template: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to remove event template. Please try again later.",
        }


def get_event_template(user, tag):
    """
    Retrieves an event template from DynamoDB, resolves the assistant via alias,
    and fetches the actual API key associated with the event.

    Args:
        user (str): The user retrieving the event template.
        tag (str): The tag of the event template.
        access_token (str): Authorization token to fetch the API key.

    Returns:
        dict: {success, data, message}
    """
    try:
        # Step 1: Retrieve the event template from DynamoDB
        response = event_table.get_item(Key={"user": user, "tag": tag})
        if "Item" not in response:
            return {
                "success": False,
                "message": f"No event template found for tag '{tag}' and user '{user}'. "
                f"Check if the tag is correct or if the template was removed.",
            }

        event_template = response["Item"]

        # Step 2: Resolve the assistant if it exists
        if "assistantId" in event_template:
            assistant_response = get_assistant_by_alias(
                user, event_template["assistantId"]
            )
            if not assistant_response["success"]:
                return {
                    "success": False,
                    "message": f"The event template exists, but its associated assistant could not be found. "
                    f"Double-check the assistantId or verify that the assistant was not deleted.",
                }
            event_template["assistant"] = assistant_response["data"]

        return {
            "success": True,
            "data": event_template,
            "message": "Event template retrieved successfully.",
        }

    except ClientError as e:
        logger.error("Error retrieving event template: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to retrieve event template. Please try again later.",
        }


def list_event_templates_for_user(user):
    """
    Retrieves all event templates for a given user and resolves their assistant aliases.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = event_table.query(
            KeyConditionExpression=Key("user").eq(user),
            ProjectionExpression="user, tag, assistantId, prompt",
        )
        event_templates = response.get("Items", [])

        if not event_templates:
            return {
                "success": False,
                "data": None,
                "message": f"No event templates found for user '{user}'. "
                f"Try adding a new event template first.",
            }

        for event_template in event_templates:
            if "assistantId" in event_template:
                assistant_response = get_assistant_by_alias(
                    user, event_template["assistantId"]
                )
                if assistant_response["success"]:
                    event_template["assistant"] = assistant_response["data"]

        return {
            "success": True,
            "data": event_templates,
            "message": "Event templates retrieved successfully.",
        }

    except ClientError as e:
        logger.error("Error listing event templates: %s", e)
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to list event templates. Please try again later.",
        }


def list_event_templates_tags_for_user(user):
    """
    Retrieves only the tags of all event templates for a given user.

    Returns:
        dict: {success, data, message} where data is a list of tags
    """
    try:
        response = event_table.query(KeyConditionExpression=Key("user").eq(user))
        event_templates = response.get("Items", [])

        if not event_templates:
            return {
                "success": False,
                "data": None,
                "message": f"No event templates found for user '{user}'. "
                f"Try adding a new event template first.",
            }

        # Extract just the tags from the event templates
        tags = [template["tag"] for template in event_templates]

        return {
            "success": True,
            "data": tags,
            "message": "Event template tags retrieved successfully.",
        }

    except ClientError as e:
        logger.error("Error listing event template tags: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to list event template tags. Please try again later.",
        }


def is_event_template_tag_available(user, tag, assistant_id=None):
    """
    Checks if an event template tag is available for a given user and assistant.

    Args:
        user (str): The user to check for.
        tag (str): The tag to check availability for.
        assistant_id (str, optional): If provided, checks if this assistant can use this tag.

    Returns:
        dict: {success, data, message} where data is a boolean indicating availability
    """
    try:
        # Check if a template with this user-tag combo exists
        response = event_table.get_item(Key={"user": user, "tag": tag})

        # If no item found, the tag is available
        if "Item" not in response:
            return {"success": True, "data": {"available": True}}
        # If tag exists but no assistant_id was provided, it's not available
        if not assistant_id:
            return {"success": True, "data": {"available": False}}

        # If assistant_id was provided, check if it matches the one in the record
        existing_assistant_id = response["Item"].get("assistantId")
        return {
            "success": True,
            "data": {"available": existing_assistant_id == assistant_id},
        }

    except ClientError as e:
        logger.error("Error checking event template tag availability: %s", e)
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to check tag availability. Please try again later.",
        }


### ==========================
### 🚀 Group Event Template Functions
### ==========================


def add_group_shared_exchange_template(
    group_id: str,
    shared_mailbox_email: str,
    assistant_id: str,
    prompt: str,
) -> dict:
    """
    Creates an event template for a group's shared Exchange inbox.
    
    Args:
        group_id (str): The group ID (e.g., "SupportTeam_abc-123").
        shared_mailbox_email (str): The shared mailbox email (e.g., "support@vanderbilt.edu").
        assistant_id (str): Assistant ID to use for processing.
        prompt (str): The event prompt template.
    
    Returns:
        dict: {success, data, message}
    """
    try:
        # Verify assistant exists
        response = get_assistant_by_alias(group_id, assistant_id)
        if not response["success"]:
            return {
                "success": False,
                "message": f"Cannot add group exchange template: {response['message']}",
            }

        # Extract API owner ID from group_id
        api_owner_id = group_id.replace("_", "/systemKey/")
        
        # Use the shared mailbox email as the "tag" for lookup
        # This allows email routing: support@vanderbilt.ai → group_id lookup
        item = {
            "user": group_id,
            "tag": shared_mailbox_email,  # Use email as tag for routing
            "prompt": prompt,
            "apiKeyId": api_owner_id,
            "assistantId": assistant_id,
            "sharedMailboxEmail": shared_mailbox_email,
            "isGroupTemplate": True,  # Flag to identify group templates
        }

        event_table.put_item(Item=item)

        return {
            "success": True,
            "message": f"Group exchange template for '{shared_mailbox_email}' successfully added for group '{group_id}'.",
            "data": {"group_id": group_id, "shared_mailbox": shared_mailbox_email}
        }

    except ClientError as e:
        logger.error("Error adding group exchange template: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to add group exchange template. Please try again later.",
        }


def remove_group_shared_exchange_template(
    group_id: str, shared_mailbox_email: str
) -> dict:
    """
    Removes a group's shared Exchange inbox event template.
    
    Args:
        group_id (str): The group ID.
        shared_mailbox_email (str): The shared mailbox email to remove.
    
    Returns:
        dict: {success, message}
    """
    try:
        # Tag is the shared mailbox email
        response = event_table.get_item(Key={"user": group_id, "tag": shared_mailbox_email})
        
        if "Item" not in response:
            return {
                "success": False,
                "message": f"No template found for mailbox '{shared_mailbox_email}' in group '{group_id}'.",
            }

        event_table.delete_item(Key={"user": group_id, "tag": shared_mailbox_email})
        
        return {
            "success": True,
            "message": f"Shared exchange template for '{shared_mailbox_email}' removed successfully.",
        }

    except ClientError as e:
        logger.error("Error removing group exchange template: %s", e)
        return {
            "success": False,
            "message": "Server error: Unable to remove group exchange template. Please try again later.",
        }


def list_group_shared_exchange_templates(group_id: str) -> dict:
    """
    Lists all shared Exchange inbox templates for a group.
    
    Args:
        group_id (str): The group ID.
    
    Returns:
        dict: {success, data, message}
    """
    try:
        response = event_table.query(
            KeyConditionExpression=Key("user").eq(group_id)
        )
        
        templates = response.get("Items", [])
        
        # Filter for only group templates (those with shared mailbox)
        group_templates = [
            t for t in templates if t.get("isGroupTemplate") or t.get("sharedMailboxEmail")
        ]
        
        if not group_templates:
            return {
                "success": True,
                "data": [],
                "message": f"No shared exchange templates found for group '{group_id}'.",
            }

        return {
            "success": True,
            "data": group_templates,
            "message": "Group shared exchange templates retrieved successfully.",
        }

    except ClientError as e:
        logger.error("Error listing group exchange templates: %s", e)
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to list group exchange templates. Please try again later.",
        }
