### ==========================
### Allowed Sender Functions
### ==========================
import os
import re
import traceback

import boto3
from botocore.exceptions import ClientError

# Initialize AWS resources
dynamodb = boto3.resource("dynamodb")

# Environment Variables
email_settings_table = dynamodb.Table(os.getenv("EMAIL_SETTINGS_DYNAMO_TABLE"))


### ==========================
### ✉️ Allowed Sender Functions
### ==========================


def add_allowed_sender(user_email: str, tag: str, sender: str):
    """
    Adds a sender to the allowedSenders list in DynamoDB for a given user and tag.

    Args:
        user_email (str): The user's email (recipient).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email or regex pattern to allow.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_email, "tag": tag})
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
                "email": user_email,
                "tag": tag,
                "allowedSenders": list(allowed_senders),
            }
        )

        return {
            "success": True,
            "message": f"Sender '{sender}' was successfully added to the allowed list for tag '{tag}'.",
        }

    except ClientError as e:
        print(f"Error adding allowed sender: {e}")
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to add the allowed sender. Please try again later.",
        }


def remove_allowed_sender(user_email: str, tag: str, sender: str):
    """
    Removes a sender from the allowedSenders list in DynamoDB for a given user and tag.

    Args:
        user_email (str): The user's email (recipient).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email or regex pattern to remove.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_email, "tag": tag})
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
            email_settings_table.delete_item(Key={"email": user_email, "tag": tag})
        else:
            email_settings_table.update_item(
                Key={"email": user_email, "tag": tag},
                UpdateExpression="SET allowedSenders = :s",
                ExpressionAttributeValues={":s": list(allowed_senders)},
            )

        return {
            "success": True,
            "message": f"Sender '{sender}' was successfully removed from the allowed list for tag '{tag}'.",
        }

    except ClientError as e:
        print(f"Error removing allowed sender: {e}")
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to remove the allowed sender. Please try again later.",
        }


def is_allowed_sender(owner_email: str, tag: str, sender: str) -> bool:
    """
    Checks if a sender is allowed to send emails to a specific user and tag.

    Args:
        owner_email (str): The owner's email (recipient).
        user_email (str): The recipient's email (user).
        tag (str): The tag associated with the recipient.
        sender (str): The sender email to check.

    Returns:
        bool: True if the sender is allowed, False otherwise.
    """
    try:
        print(
            f"Checking allowed sender for user '{owner_email}', tag '{tag}', sender '{sender}'"
        )

        if sender == owner_email:
            return True  # Owner is always allowed

        response = email_settings_table.get_item(Key={"email": owner_email, "tag": tag})
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
                print(f"Invalid regex pattern '{pattern_str}': {e}")

        return False  # Not allowed

    except ClientError as e:
        print(f"Error checking allowed sender: {e}")
        traceback.print_exc()
        return False


def list_allowed_senders(user_email: str, tag: str):
    """
    Lists all allowed senders for a given user and tag.

    Args:
        user_email (str): The user's email (recipient).
        tag (str): The tag associated with the recipient.

    Returns:
        dict: {success, data, message}
    """
    try:
        response = email_settings_table.get_item(Key={"email": user_email, "tag": tag})
        item = response.get("Item", {})

        allowed_senders = item.get("allowedSenders", [])

        return {
            "success": True,
            "data": allowed_senders,
            "message": f"Retrieved {len(allowed_senders)} allowed sender(s) for tag '{tag}'.",
        }

    except ClientError as e:
        print(f"Error listing allowed senders: {e}")
        traceback.print_exc()
        return {
            "success": False,
            "data": [],
            "message": "Server error: Unable to list allowed senders. Please try again later.",
        }
