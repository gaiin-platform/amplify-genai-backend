
import base64
import email
from email import policy
import re
import os
import boto3
from pycommon.logger import getLogger
from botocore.exceptions import ClientError
logger = getLogger("agent_email_events")

def extract_email_body_and_attachments(sns_message):
    # The given steps remain the same, except for the part dealing with content disposition
    encoded_content = sns_message["content"]

    logger.info(f"Email content size (base64): {len(encoded_content)} bytes")

    # Decode the Base64-encoded email content
    decoded_content = base64.b64decode(encoded_content)
    logger.info(f"Email content size (decoded): {len(decoded_content)} bytes")

    # Parse the content into an email.message.EmailMessage object
    email_message = email.message_from_bytes(decoded_content, policy=policy.default)
    attachments = []
    body_plain = None
    body_html = None

    part_count = 0
    for part in email_message.walk():
        part_count += 1
        content_type = part.get_content_type()
        content_disposition = part.get("Content-Disposition")
        filename = part.get_filename()

        logger.info(f"Part {part_count}: type={content_type}, disposition={content_disposition}, filename={filename}")

        if content_disposition:  # This part is an attachment or inlined content
            # Use the get_content_disposition() method to check disposition type
            disposition = part.get_content_disposition()
            logger.info(f"Part {part_count} disposition method: {disposition}")
            if disposition == "attachment" or (
                disposition == "inline" and part.get_filename()
            ):
                attachment_data = part.get_payload(decode=True)
                logger.info(f"Found attachment: {filename}, size: {len(attachment_data) if attachment_data else 0} bytes")
                attachments.append(
                    {
                        "filename": part.get_filename(),
                        "content": attachment_data,
                        "content_type": content_type,
                    }
                )
        elif content_type == "text/plain" and body_plain is None:  # Plain text body
            body_plain = part.get_payload(decode=True)
            logger.info(f"Found plain text body: {len(body_plain) if body_plain else 0} bytes")
        elif content_type == "text/html" and body_html is None:  # HTML body
            body_html = part.get_payload(decode=True)
            logger.info(f"Found HTML body: {len(body_html) if body_html else 0} bytes")

    logger.info(f"Total parts processed: {part_count}, Attachments found: {len(attachments)}")

    # Return the extracted content with safe decoding
    return {
        "body_plain": safe_decode(body_plain) if body_plain else None,
        "body_html": safe_decode(body_html) if body_html else None,
        "attachments": attachments,
    }



def safe_decode(byte_content):
    """Safely decode byte content, trying UTF-8 first then falling back to more permissive encodings."""
    if not byte_content:
        return None

    # Try UTF-8 first
    try:
        return byte_content.decode("utf-8")
    except UnicodeDecodeError:
        # Fall back to latin-1 (ISO-8859-1) which can handle any byte value
        try:
            return byte_content.decode("latin-1")
        except:
            # If all else fails, use UTF-8 with replacement for invalid chars
            return byte_content.decode("utf-8", errors="replace")




def lookup_username_from_cognito_table(email_address):
    """
    Direct lookup of username from email address using Cognito users table.
    This bypasses API authentication requirements.
    
    Handles two cases:
    1. User has email field populated → match email field, return user_id
    2. User has no email field → check if user_id itself matches the email
    
    Args:
        email_address (str): Full email like "karely.rodriguez@vanderbilt.edu"
        
    Returns:
        str: Username like "rodrikm1" or None if not found
    """
    if not email_address:
        return None
        
    try:
        dynamodb = boto3.resource("dynamodb")
        
        # Use the same table that object-access service uses
        table_name = os.environ.get("COGNITO_USERS_DYNAMODB_TABLE")
        if not table_name:
            logger.warning("COGNITO_USERS_DYNAMODB_TABLE not configured")
            return None
            
        cognito_user_table = dynamodb.Table(table_name)
        email_lower = email_address.lower()
        
        # Strategy 1: Look for users where user_id itself matches the email
        # (for cases where user_id is the email)
        try:
            response = cognito_user_table.get_item(
                Key={"user_id": email_lower}
            )
            
            if response.get("Item"):
                # Found match where user_id is the email
                item = response["Item"]
                username = item.get("user_id")
                if username:
                    logger.info("Cognito user_id field match: %s -> %s", email_address, username)
                    return username
        except ClientError:
            # Item not found, continue to next strategy
            pass
        
        # Strategy 2: Look for users where email field matches
        response = cognito_user_table.scan(
            ProjectionExpression="user_id, email",
            FilterExpression="email = :target_email",
            ExpressionAttributeValues={
                ":target_email": email_lower
            }
        )
        
        if response.get("Items"):
            # Found exact match in email field
            item = response["Items"][0]
            username = item.get("user_id")
            if username:
                logger.info("Cognito email field match: %s -> %s", email_address, username)
                return username
                
        logger.info("No Cognito table match found for email: %s", email_address)
        return None
        
    except Exception as e:
        logger.error("Error looking up username in Cognito table: %s", e)
        return None


def lookup_username_from_email(email_address):
    """
    Look up username from email address using direct Cognito table lookup.
    
    Args:
        email_address (str): Full email like "karely.rodriguez@vanderbilt.edu"
        
    Returns:
        str: Username like "rodrikm1" or fallback to email prefix if not found
    """
    if not email_address:
        return None
    
    # Direct Cognito table lookup (no API key required)
    direct_username = lookup_username_from_cognito_table(email_address)
    if direct_username:
        return direct_username
    
    # Fallback: extract username portion from email
    username_fallback = email_address.split('@')[0]
    logger.info("Username lookup fallback for %s -> %s", email_address, username_fallback)
    return username_fallback


def parse_email(email):
    pattern = re.compile(r"^(?P<user>[^+@]+)(\+(?P<tag>[^@]+))?@(?P<domain>[^@]+)$")
    match = pattern.match(email)

    if match:
        return match.groupdict()
    else:
        raise ValueError("Invalid email address format")


def extract_destination_emails(message):
    """
    Extract destination email addresses from an SES SNS message.

    Args:
        message (dict): SNS message containing SES notification

    Returns:
        list: List of destination email addresses (lowercase), or empty list if not found
    """
    import json

    try:
        # Parse the SNS message if it's a string
        if isinstance(message.get("Message"), str):
            ses_content = json.loads(message["Message"])
        else:
            ses_content = message.get("Message", {})

        # Extract destination emails from mail.destination
        destination_emails = ses_content.get('mail', {}).get('destination', [])

        # Normalize to lowercase
        return [email.lower() for email in destination_emails]

    except (KeyError, json.JSONDecodeError, AttributeError) as e:
        logger.error("Error extracting destination emails: %s", e)
        return []


def is_ses_message(message):
    """
    Check if a message is a valid SES notification.

    Args:
        message (dict): SNS message to check

    Returns:
        bool: True if message is a valid SES notification
    """
    import json

    try:
        # Parse the SNS message if it's a string
        if isinstance(message.get("Message"), str):
            ses_content = json.loads(message["Message"])
        else:
            ses_content = message.get("Message", {})

        # Check for required SES notification fields
        return all(
            k in ses_content for k in ["notificationType", "mail", "receipt"]
        )

    except (KeyError, json.JSONDecodeError, AttributeError):
        return False
