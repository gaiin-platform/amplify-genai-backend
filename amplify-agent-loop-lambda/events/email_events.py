import hashlib
import json
import re
import os
import uuid
from datetime import datetime
import base64
import email
from email import policy
from string import Template
from typing import Dict, Any

import boto3
from botocore.exceptions import ClientError

from events.event_handler import MessageHandler
from events.email_sender_controls import is_allowed_sender
from events.event_templates import get_event_template
from delegation.api_keys import get_api_key_directly_by_id
from service.conversations import register_agent_conversation
from pycommon.const import IMAGE_FILE_TYPES
from pycommon.logger import getLogger
logger = getLogger("agent_email_events")

organization_email_domain = os.environ["ORGANIZATION_EMAIL_DOMAIN"]



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


def get_item_from_dynamodb(email, tag):
    dynamodb = boto3.resource("dynamodb")
    table_name = os.environ["EMAIL_SETTINGS_DYNAMO_TABLE"]
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={"email": email, "tag": tag})
        return response.get("Item", None)
    except ClientError as e:
        raise Exception(f"An error occurred: {e.response['Error']['Message']}")


def check_allowed_senders(allowed_patterns, input_string):
    for pattern_str in allowed_patterns:
        try:
            pattern = re.compile(pattern_str)
            if pattern.match(input_string):
                logger.info("Matched sender pattern: %s", pattern_str)
                return True
        except re.error as e:
            logger.error("Invalid regex: %s - %s", pattern_str, e)
    return False


def get_target_s3_key_base(email, tag, email_id):
    # Turn the current date into a string
    dt_string = datetime.now().strftime("%Y-%m-%d")

    return f"{email}/ingest/email/{tag}/{dt_string}/{email_id}"


def extract_email_body_and_attachments(sns_message):
    # The given steps remain the same, except for the part dealing with content disposition
    encoded_content = sns_message["content"]

    # Decode the Base64-encoded email content
    decoded_content = base64.b64decode(encoded_content)

    # Parse the content into an email.message.EmailMessage object
    email_message = email.message_from_bytes(decoded_content, policy=policy.default)
    attachments = []
    body_plain = None
    body_html = None

    for part in email_message.walk():
        content_type = part.get_content_type()
        content_disposition = part.get("Content-Disposition")

        if content_disposition:  # This part is an attachment or inlined content
            # Use the get_content_disposition() method to check disposition type
            disposition = part.get_content_disposition()
            if disposition == "attachment" or (
                disposition == "inline" and part.get_filename()
            ):
                attachment_data = part.get_payload(decode=True)
                attachments.append(
                    {
                        "filename": part.get_filename(),
                        "content": attachment_data,
                        "content_type": content_type,
                    }
                )
        elif content_type == "text/plain" and body_plain is None:  # Plain text body
            body_plain = part.get_payload(decode=True)
        elif content_type == "text/html" and body_html is None:  # HTML body
            body_html = part.get_payload(decode=True)

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


def find_hash_tags(text):
    """
    Finds all tags that start with '#' in the given text, strips off the '#', and returns them as a list.
    """
    return [tag[1:] for tag in re.findall(r"#\w+", text)]


def sanitize_filename(filename):
    """Sanitize the filename to be safe for S3 keys."""
    # Replace ".." with ".", remove leading/trailing periods or slashes
    sanitized = re.sub(r"\.{2,}", ".", filename).strip(".")
    # Replace any remaining special characters with an underscore
    sanitized = re.sub(r"[^\w\-_\.]", "_", sanitized)
    return sanitized


def save_email_to_s3(current_user, email_details, tags):
    # Create an S3 client
    s3 = boto3.client("s3")

    email_content = email_details["contents"]
    # Determine the body content to save (1)
    body = (
        email_content["body_plain"]
        if email_content["body_plain"]
        else email_content["body_html"]
    )

    # create a random uuid for the email
    email_subject = email_details["subject"]
    email_sender = email_details["sender"]
    email_time = (email_details["timestamp"],)
    email_base_name = f"Email {email_subject} from {email_sender} at {email_time}"
    email_file_name = f"{email_base_name}.json"
    bucket_name, body_key = create_file_metadata_entry(
        current_user, email_file_name, "application/json", tags, {}, "email"
    )

    email_to_save_string = (
        f"timestamp: {email_details['timestamp']}\n"
        f"subject: {email_details['subject']}\n"
        f"sender: {email_details['sender']}\n"
        f"recipients: {', '.join(email_details['recipients'])}\n"
        f"body:\n-----\n{body}\n-----\n"
        f"attachment_file_names: {', '.join([sanitize_filename(attachment['filename']) for attachment in email_content['attachments']])}"
    )

    # Check if the target key already exists and just return True if it does
    try:
        s3.head_object(Bucket=bucket_name, Key=body_key)
        logger.info("Email already exists in s3://%s/%s", bucket_name, body_key)
        return True
    except ClientError:
        pass

    # Save the body content to S3
    s3.put_object(Bucket=bucket_name, Key=body_key, Body=email_to_save_string)

    logger.info("Saved email body to s3://%s/%s", bucket_name, body_key)

    # Loop through and save all attachments (2)
    for attachment in email_content["attachments"]:
        file_name = attachment["filename"]
        file_name = sanitize_filename(file_name)
        file_content = attachment["content"]

        content_type = attachment["content_type"]
        attach_bucket_name, attach_body_key = create_file_metadata_entry(
            current_user, file_name, content_type, tags, {}, "email"
        )
        # Save the file to S3
        s3.put_object(Bucket=attach_bucket_name, Key=attach_body_key, Body=file_content)

        logger.info("Saved attachment to s3://%s/%s", attach_bucket_name, attach_body_key)


def index_email(user_email, project_tag, email_id, email_details):

    logger.info("Email ID: %s", email_id)
    s3_key = get_target_s3_key_base(user_email, project_tag, email_id)
    logger.info("S3 Key Base: %s", s3_key)

    email_subject = email_details["subject"]
    email_subject_tags = find_hash_tags(email_subject)
    logger.info("Email Subject Tags: %s", email_subject_tags)

    all_tags = [project_tag]
    if email_subject_tags and len(email_subject_tags) > 0:
        logger.info("Updating tags for email %s", email_id)
        all_tags.extend(email_subject_tags)

    # Save the email to S3
    save_email_to_s3(user_email, email_details, all_tags)
    return True


def process_email(event, context):
    ses_notification = event["Records"][0]["Sns"]["Message"]
    ses_notification = json.loads(ses_notification)

    # Check if any spam check failed
    if (
        ses_notification["receipt"]["spfVerdict"]["status"] == "FAIL"
        or ses_notification["receipt"]["dkimVerdict"]["status"] == "FAIL"
        or ses_notification["receipt"]["spamVerdict"]["status"] == "FAIL"
        or ses_notification["receipt"]["virusVerdict"]["status"] == "FAIL"
    ):
        logger.info("Dropping spam")
        # Stop processing rule set, dropping message
        return {"disposition": "STOP_RULE_SET"}
    else:

        source_email = ses_notification["mail"]["source"]
        if isinstance(source_email, str):
            source_email = source_email.lower()

        destination_emails = ses_notification["mail"]["destination"]

        logger.info("Source Email: %s", source_email)
        logger.info("Destination Emails: %s", destination_emails)

        parsed_source_email = parse_email(source_email)
        logger.info("Parsed Source Email: %s", json.dumps(parsed_source_email, indent=2))
        parsed_destination_emails = [parse_email(email) for email in destination_emails]

        for email in parsed_destination_emails:
            logger.info("Parsed Destination Email: %s", json.dumps(email, indent=2))

            tag = email["tag"] if email["tag"] else "default"
            email["tag"] = tag
            logger.info("Tag: %s", tag)

            target_email_lookup = f"{email['user']}@{email['domain']}"
            logger.info("Target Email Lookup: %s :: %s", target_email_lookup, tag)
            
            # Construct the full email and then convert to username
            owner_email = f"{email['user']}@{organization_email_domain}"
            owner_username = lookup_username_from_email(owner_email)
            
            logger.info("Owner Email: %s -> Username: %s", owner_email, owner_username)
            logger.info("Source Email: %s", source_email)

            # Use is_allowed_sender function - pass both email and username
            if is_allowed_sender(owner_username, tag, source_email, owner_email=owner_email):
                logger.info("Sender %s is allowed", source_email)
                return to_agent_event(email, source_email, ses_notification, owner_username)

            logger.warning("Sender %s is NOT allowed", source_email)
            return None

        return None


def create_file_metadata_entry(
    current_user, name, file_type, tags, data_props, knowledge_base
):
    dynamodb = boto3.resource("dynamodb")
    bucket_name = os.environ[
        (
            "S3_IMAGE_INPUT_BUCKET_NAME"
            if (file_type in IMAGE_FILE_TYPES)
            else "S3_RAG_INPUT_BUCKET_NAME"
        )
    ]
    dt_string = datetime.now().strftime("%Y-%m-%d")
    key = f"{current_user}/{dt_string}/{uuid.uuid4()}.json"

    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    files_table.put_item(
        Item={
            "id": key,
            "name": name,
            "type": file_type,
            "tags": tags,
            "data": data_props,
            "knowledgeBase": knowledge_base,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
            "createdBy": current_user,
            "updatedBy": current_user,
        }
    )

    if tags is not None and len(tags) > 0:
        update_file_tags(current_user, key, tags)

    return bucket_name, key


def update_file_tags(current_user, item_id, tags):
    # Helper function that updates tags in DynamoDB and adds tags to the user
    table_name = os.environ[
        "FILES_DYNAMO_TABLE"
    ]  # Get the table name from the environment variable

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={"id": item_id})
        item = response.get("Item")

        if item and item.get("createdBy") == current_user:
            # Update the item's tags in DynamoDB
            table.update_item(
                Key={"id": item_id},
                UpdateExpression="SET tags = :tags",
                ExpressionAttributeValues={":tags": tags},
            )

            # Add tags to the user
            tags_added = add_tags_to_user(current_user, tags)
            if tags_added["success"]:
                return True, "Tags updated and added to user"
            else:
                return False, f"Error adding tags to user: {tags_added['message']}"

        else:
            return False, "File not found or not authorized to update tags"

    except ClientError as e:
        logger.error("Unable to update tags: %s", e.response['Error']['Message'])
        return False, "Unable to update tags"


def add_tags_to_user(current_user, tags_to_add):
    """Add a tag to user's list of tags if it doesn't already exist."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_TAGS_DYNAMO_TABLE"])

    try:
        response = table.update_item(
            Key={"user": current_user},
            UpdateExpression="ADD #tags :tags",
            ExpressionAttributeNames={
                "#tags": "tags",  # Assuming 'Tags' is the name of the attribute
            },
            ExpressionAttributeValues={
                ":tags": set(tags_to_add)  # The tags to add as a set
            },
            ReturnValues="UPDATED_NEW",
        )
        logger.info("Tags added successfully to user ID: %s", current_user)
        return {"success": True, "message": "Tags added successfully"}

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ValidationException":
            # If the item doesn't exist, create it with the specified tags
            response = table.put_item(
                Item={"UserID": current_user, "tags": set(tags_to_add)}
            )
            logger.info("New user created with tags for user ID: %s", current_user)
            return {"success": True, "message": "Tags added successfully"}
        else:
            logger.error(
                "Error adding tags to user ID: %s: %s", current_user, e.response['Error']['Message']
            )
            return {"success": False, "message": e.response["Error"]["Message"]}


def to_agent_event(parsed_destination_email, source_email, ses_notification, owner_username=None):
    """
    Processes an incoming email, looks up an event mapping in DynamoDB based on user and tags,
    fills in the event template (including fetching the actual API key), and returns the formatted event.

    Args:
        parsed_destination_email (dict): Parsed email details, including user and tag.
        source_email (str): Sender's email address.
        ses_notification (dict): SES notification payload.

    Returns:
        dict: Formatted event for the agent, or None if no event match is found.
    """

    logger.info("Routing email from %s to %s", source_email, parsed_destination_email)

    # Extract email metadata
    mail_data = ses_notification["mail"]
    common_headers = mail_data.get("commonHeaders", {})
    email_details = {
        "sender": source_email,
        "timestamp": mail_data["timestamp"],
        "subject": common_headers.get("subject", "No Subject"),
        "recipients": mail_data["destination"],
        "cc": common_headers.get("cc", "No CC'd emails"),
        "bcc": common_headers.get("bcc", "No BCC'd emails"),
    }

    # Extract email body and attachments
    parsed_email = extract_email_body_and_attachments(ses_notification)
    email_details["contents"] = parsed_email.get(
        "body_plain", None
    ) or parsed_email.get("body_html", "No content available.")

    # Generate a unique email hash (to prevent duplicate processing)
    serialized_data = json.dumps(email_details, default=str).encode("utf-8")
    email_id = hashlib.sha256(serialized_data).hexdigest()
    email_details["received_time"] = datetime.utcnow().isoformat()

    # Determine user and project tag - use provided username or construct from email
    if owner_username:
        user = owner_username
        logger.info("Using provided username: %s", user)
    else:
        # Fallback to email construction and username lookup
        constructed_email = f"{parsed_destination_email['user']}@{organization_email_domain}"
        user = lookup_username_from_email(constructed_email)
        logger.info("Constructed email: %s -> Username: %s", constructed_email, user)

    project_tag = parsed_destination_email.get("tag", "email")
    logger.info("Project Tag: %s", project_tag)

    # Extract tags from email subject
    email_subject_tags = find_hash_tags(email_details["subject"])
    logger.info("Email Subject Tags: %s", email_subject_tags)

    # Prioritize matching order: project_tag first, then email subject tags
    all_tags = [project_tag] + (email_subject_tags if email_subject_tags else [])
    logger.info("All Tags: %s", all_tags)

    # Attempt to find a matching event in DynamoDB
    event_data = None
    for tag in all_tags:
        logger.info("Looking up event for user '%s' and tag '%s'", user, tag)

        # Use `get_event_template` to retrieve the event and its resolved API key
        event_response = get_event_template(user, tag)

        if event_response["success"]:
            event_data = event_response["data"]
            # retrive api key from table directly
            api_result = get_api_key_directly_by_id(event_data["apiKeyId"])
            if not api_result["success"]:
                return None

            event_data["apiKey"] = api_result["apiKey"]

            logger.info(
                "Found matching event for user '%s' and tag '%s': %s", user, tag, event_data
            )
            break  # Stop searching once a match is found

    if not event_data:
        logger.info("No matching event found in DynamoDB. Indexing email")
        # reset to full contents of email
        email_details["contents"] = parsed_email
        index_email(user, project_tag, email_id, email_details)
        return None

    # Fill in the event template using email details, including full body text
    formatted_prompt = []
    prompts = event_data.get(
        "prompt",
        [
            {
                "role": "user",
                "content": "I have received an email from ${sender} at ${timestamp}. The subject of the email is: '${subject}'. The email was sent to: ${recipients}. The contents of the email are:\n\n'''${contents}'''",
            }
        ],
    )
    for entry in prompts:
        content_template = entry.get("content", "")

        try:
            # Use Python string templating to replace placeholders with actual email details
            formatted_content = Template(content_template).safe_substitute(
                email_details
            )
        except KeyError as e:
            logger.error("Error filling template: missing key %s", e)
            formatted_content = content_template  # Fallback to original if missing data

        formatted_prompt.append({"role": entry["role"], "content": formatted_content})
    logger.info("Formatted Prompt: %s", formatted_prompt)
    # Construct final event to pass to the agent
    event_payload = {
        "currentUser": user,
        "sessionId": email_id,
        "prompt": formatted_prompt,
        "metadata": {
            "accessToken": event_data.get("apiKey", None),
            "source": "SES",
            "eventId": email_id,
            "timestamp": email_details["received_time"],
            "requestContent": email_details["contents"],
            "assistant": event_data.get("assistant", {}),
            "files": [],  # extract_file_metadata(parsed_email.get("attachments", []))
        },
    }

    return event_payload


def extract_file_metadata(attachments):
    """
    Extracts metadata from email attachments.

    Args:
        attachments (list): List of attachments.

    Returns:
        list: A list of dictionaries containing file metadata.
    """
    return [
        {
            "fileId": hashlib.md5(att["name"].encode()).hexdigest(),  # Unique file ID
            "originalName": att["name"],
            "downloadUrl": att.get("s3_url", ""),
            "size": att.get("size", 0),
        }
        for att in attachments
    ]


class SESMessageHandler(MessageHandler):
    def can_handle(self, message: Dict[str, Any]) -> bool:
        try:
            ses_content = json.loads(message["Message"])
            logger.debug("notificationType: %s", ses_content.get('notificationType'))
            logger.debug("mail: %s", ses_content.get('mail'))
            logger.debug("receipt: %s", ses_content.get('receipt'))
            can_handle = all(
                k in ses_content for k in ["notificationType", "mail", "receipt"]
            )
            logger.debug("SESMessageHandler can_handle: %s", can_handle)
            return can_handle
        except (KeyError, json.JSONDecodeError) as e:
            logger.error("Error: %s", e)
            logger.warning("SESMessageHandler cannot handle message")
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        sns_message = {"Records": [{"Sns": {"Message": message["Message"]}}]}
        event = process_email(sns_message, context)
        return event

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        logger.error("SESMessageHandler onFailure: %s", error)
        pass

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        metadata = agent_input_event.get("metadata", {})
        accessToken = metadata.get("accessToken")

        if accessToken:
            logger.info("Registering agent conversation")
            register_agent_conversation(
                access_token=accessToken, input=agent_input_event, result=agent_result
            )
        else:
            logger.warning("No access token found")
