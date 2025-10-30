### ==========================
### Allowed Sender Functions
### ==========================
import os
import re
import traceback

import boto3
from botocore.exceptions import ClientError
from pycommon.logger import getLogger
logger = getLogger("agent_email_events")


# Initialize AWS resources
dynamodb = boto3.resource("dynamodb")

# Environment Variables
email_settings_table = dynamodb.Table(os.getenv("EMAIL_SETTINGS_DYNAMO_TABLE"))


### ==========================
### ✉️ Allowed Sender Functions
### ==========================


def add_allowed_sender(user_username: str, tag: str, sender: str):
    """
    Adds a sender to the allowedSenders list in DynamoDB for a given user and tag.

    Args:
        user_username (str): The user's username (for database lookup).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email or regex pattern to allow.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_username, "tag": tag})
        item = response.get("Item", {})

        allowed_senders = set(item.get("allowedSenders", []))
        sender = sender.lower()

        if sender in allowed_senders:
            return {
                "success": True,
                "message": f"Sender '{sender}' is already in the allowed list for tag '{tag}'. No action was taken.",
            }

        allowed_senders.add(sender)

        email_settings_table.put_item(
            Item={
                "email": user_username,
                "tag": tag,
                "allowedSenders": list(allowed_senders),
            }
        )

        return {
            "success": True,
            "message": f"Sender '{sender}' was successfully added to the allowed list for tag '{tag}'.",
        }

    except ClientError as e:
        logger.error("Error adding allowed sender: %s", e, exc_info=True)
        return {
            "success": False,
            "message": "Server error: Unable to add the allowed sender. Please try again later.",
        }


def remove_allowed_sender(user_username: str, tag: str, sender: str):
    """
    Removes a sender from the allowedSenders list in DynamoDB for a given user and tag.

    Args:
        user_username (str): The user's username (for database lookup).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email or regex pattern to remove.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_username, "tag": tag})
        item = response.get("Item", {})

        allowed_senders = set(item.get("allowedSenders", []))

        if sender == "*":
            allowed_senders = set()
        elif sender not in allowed_senders and sender.lower() not in allowed_senders:
            return {
                "success": False,
                "message": f"Sender '{sender}' was not found in the allowed list for tag '{tag}'. Nothing to remove.",
            }
        else:
            allowed_senders.discard(sender)
            allowed_senders.discard(sender.lower())

        if not allowed_senders:
            email_settings_table.delete_item(Key={"email": user_username, "tag": tag})
        else:
            email_settings_table.update_item(
                Key={"email": user_username, "tag": tag},
                UpdateExpression="SET allowedSenders = :s",
                ExpressionAttributeValues={":s": list(allowed_senders)},
            )

        return {
            "success": True,
            "message": f"Sender '{sender}' was successfully removed from the allowed list for tag '{tag}'.",
        }

    except ClientError as e:
        logger.error("Error removing allowed sender: %s", e, exc_info=True)
        return {
            "success": False,
            "message": "Server error: Unable to remove the allowed sender. Please try again later.",
        }


def is_allowed_sender(owner_username: str, tag: str, sender: str, owner_email: str = None) -> bool:
    """
    Checks if a sender is allowed to send emails to a specific user and tag.
    
    Args:
        owner_username (str): The owner's username (for database lookup).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email to check.
        owner_email (str): Optional owner's email (for permission comparison).

    Returns:
        bool: True if the sender is allowed, False otherwise.
    """
    try:
        logger.info(
            "Checking allowed sender for user '%s', tag '%s', sender '%s'", owner_username, tag, sender
        )

        # Owner is always allowed to email their own assistant
        if owner_email and sender.lower() == owner_email.lower():
            logger.info("Sender matches owner email - access granted")
            return True

        # Database lookup using username (post-migration key structure)
        response = email_settings_table.get_item(Key={"email": owner_username, "tag": tag})
        item = response.get("Item", {})

        allowed_senders = item.get("allowedSenders", [])

        if not allowed_senders:
            return False  # No senders allowed

        if sender in allowed_senders:
            return True  # Explicitly allowed

        for pattern_str in allowed_senders:
            try:
                pattern = re.compile(pattern_str)
                if pattern.match(sender):
                    return True  # Matches regex pattern
            except re.error as e:
                logger.error("Invalid regex pattern '%s': %s", pattern_str, e)

        return False  # Not allowed

    except ClientError as e:
        logger.error("Error checking allowed sender: %s", e, exc_info=True)
        return False


def list_allowed_senders(user_username: str, tag: str):
    """
    Lists all allowed senders for a given user and tag.

    Args:
        user_username (str): The user's username (for database lookup).
        tag (str): The tag associated with the recipient.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_username, "tag": tag})
        item = response.get("Item", {})

        allowed_senders = item.get("allowedSenders", [])

        return {
            "success": True,
            "data": allowed_senders,
            "message": f"Retrieved {len(allowed_senders)} allowed sender(s) for tag '{tag}'.",
        }

    except ClientError as e:
        logger.error("Error listing allowed senders: %s", e, exc_info=True)
        return {
            "success": False,
            "data": [],
            "message": "Server error: Unable to list allowed senders. Please try again later.",
        }
