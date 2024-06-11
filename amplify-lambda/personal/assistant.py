
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import hashlib
import json
import re
import os
import uuid
from datetime import datetime
import base64
import email
from email import policy
import boto3
from botocore.exceptions import ClientError
from assistant.assistant import update_file_tags, create_file_metadata_entry

organization_email_domain = os.environ['ORGANIZATION_EMAIL_DOMAIN']


def parse_email(email):
    pattern = re.compile(r'^(?P<user>[^+@]+)(\+(?P<tag>[^@]+))?@(?P<domain>[^@]+)$')
    match = pattern.match(email)

    if match:
        return match.groupdict()
    else:
        raise ValueError("Invalid email address format")


def get_item_from_dynamodb(email, tag):
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ['EMAIL_SETTINGS_DYNAMO_TABLE']
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={'email': email, 'tag': tag})
        return response.get('Item', None)
    except ClientError as e:
        raise Exception(f"An error occurred: {e.response['Error']['Message']}")


def check_allowed_senders(allowed_patterns, input_string):
    for pattern_str in allowed_patterns:
        try:
            pattern = re.compile(pattern_str)
            if pattern.match(input_string):
                print(f"Matched sender pattern: {pattern_str}")
                return True
        except re.error as e:
            print(f"Invalid regex: {pattern_str} - {e}")
    return False


def get_target_s3_key_base(email, tag, email_id):
    # Turn the current date into a string
    dt_string = datetime.now().strftime('%Y-%m-%d')

    return f"{email}/ingest/email/{tag}/{dt_string}/{email_id}"


def extract_email_body_and_attachments(sns_message):
    # The given steps remain the same, except for the part dealing with content disposition
    encoded_content = sns_message['content']

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
            if disposition == "attachment" or (disposition == "inline" and part.get_filename()):
                attachment_data = part.get_payload(decode=True)
                attachments.append({
                    "filename": part.get_filename(),
                    "content": attachment_data,
                    "content_type": content_type
                })
        elif content_type == "text/plain" and body_plain is None:  # Plain text body
            body_plain = part.get_payload(decode=True)
        elif content_type == "text/html" and body_html is None:  # HTML body
            body_html = part.get_payload(decode=True)

    # Return the extracted content
    return {
        "body_plain": body_plain.decode('utf-8') if body_plain else None,
        "body_html": body_html.decode('utf-8') if body_html else None,
        "attachments": attachments
    }


def find_hash_tags(text):
    """
    Finds all tags that start with '#' in the given text, strips off the '#', and returns them as a list.
    """
    return [tag[1:] for tag in re.findall(r'#\w+', text)]


def sanitize_filename(filename):
    """Sanitize the filename to be safe for S3 keys."""
    # Replace ".." with ".", remove leading/trailing periods or slashes
    sanitized = re.sub(r'\.{2,}', '.', filename).strip(".")
    # Replace any remaining special characters with an underscore
    sanitized = re.sub(r'[^\w\-_\.]', '_', sanitized)
    return sanitized


def save_email_to_s3(current_user, email_details, tags):
    # Create an S3 client
    s3 = boto3.client('s3')

    email_content = email_details['contents']
    # Determine the body content to save (1)
    body = email_content['body_plain'] if email_content['body_plain'] else email_content['body_html']

    # create a random uuid for the email
    email_subject = email_details['subject']
    email_sender = email_details['sender']
    email_time = email_details['timestamp'],
    email_base_name = f"Email {email_subject} from {email_sender} at {email_time}"
    email_file_name = f"{email_base_name}.json"
    bucket_name, body_key = create_file_metadata_entry(current_user, email_file_name, "application/json", tags, {},
                                                       "email")

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
        print(f"Email already exists in s3://{bucket_name}/{body_key}")
        return True
    except ClientError:
        pass

    # Save the body content to S3
    s3.put_object(Bucket=bucket_name, Key=body_key, Body=email_to_save_string)

    print(f"Saved email body to s3://{bucket_name}/{body_key}")

    # Loop through and save all attachments (2)
    for attachment in email_content['attachments']:
        file_name = attachment['filename']
        file_name = sanitize_filename(file_name)
        file_content = attachment['content']

        content_type = attachment['content_type']
        attach_bucket_name, attach_body_key = create_file_metadata_entry(current_user, file_name, content_type, tags,
                                                                         {}, "email")

        # Save the file to S3
        s3.put_object(Bucket=attach_bucket_name, Key=attach_body_key, Body=file_content)

        print(f"Saved attachment to s3://{attach_bucket_name}/{attach_body_key}")


def index_email(parsed_destination_email, source_email, ses_notification):
    print(f"Indexing email from {source_email} to {parsed_destination_email}")

    mail_data = ses_notification['mail']
    receipt_data = ses_notification['receipt']

    # Prepare the returned dictionary
    email_details = {
        'sender': source_email,
        'timestamp': mail_data['timestamp'],
        'subject': mail_data.get('commonHeaders', {}).get('subject', 'No Subject'),
        'recipients': mail_data['destination'],  # Extract recipients
        'contents': ses_notification['content']  # This assumes 'contents' refers to the email subject
    }

    # Create a hash of the ses_notification
    serialized_data = json.dumps(email_details).encode('utf-8')
    email_id = hashlib.sha256(serialized_data).hexdigest()
    # We wait to put the received time in so that we can detect duplicate emails based on
    # the same sender/recipients and contents.
    email_details['received_time'] = mail_data['timestamp']
    parsed_email = extract_email_body_and_attachments(ses_notification)
    email_details['contents'] = parsed_email

    user_email = f"{parsed_destination_email['user']}@{organization_email_domain}"
    project_tag = parsed_destination_email['tag'] if parsed_destination_email['tag'] else 'email'

    print(f"Email ID: {email_id}")
    s3_key = get_target_s3_key_base(user_email, project_tag, email_id)
    print(f"S3 Key Base: {s3_key}")

    email_subject = email_details['subject']
    email_subject_tags = find_hash_tags(email_subject)
    print(f"Email Subject Tags: {email_subject_tags}")

    all_tags = [project_tag]
    if email_subject_tags and len(email_subject_tags) > 0:
        print(f"Updating tags for email {email_id}")
        all_tags.extend(email_subject_tags)

    # Save the email to S3
    save_email_to_s3(user_email, email_details, all_tags)
    return True


def process_email(event, context):
    ses_notification = event['Records'][0]['Sns']['Message']
    ses_notification = json.loads(ses_notification)

    # Check if any spam check failed
    if (ses_notification['receipt']['spfVerdict']['status'] == 'FAIL' or
            ses_notification['receipt']['dkimVerdict']['status'] == 'FAIL' or
            ses_notification['receipt']['spamVerdict']['status'] == 'FAIL' or
            ses_notification['receipt']['virusVerdict']['status'] == 'FAIL'):
        print('Dropping spam')
        # Stop processing rule set, dropping message
        return {'disposition': 'STOP_RULE_SET'}
    else:

        source_email = ses_notification['mail']['source']
        destination_emails = ses_notification['mail']['destination']

        print(f"Source Email: {source_email}")
        print(f"Destination Emails: {destination_emails}")

        parsed_source_email = parse_email(source_email)
        print(f"Parsed Source Email: {json.dumps(parsed_source_email, indent=2)}")
        parsed_destination_emails = [parse_email(email) for email in destination_emails]

        for email in parsed_destination_emails:
            print(f"Parsed Destination Email: {json.dumps(email, indent=2)}")

            tag = email['tag'] if email['tag'] else 'default'
            email['tag'] = tag
            print(f"Tag: {tag}")

            target_email_lookup = f"{email['user']}@{email['domain']}"
            print(f"Target Email Lookup: {target_email_lookup} :: {tag}")
            settings = get_item_from_dynamodb(target_email_lookup, tag)
            print(f"Destination Email Settings Found: {settings != None}")

            owner_email = f"{email['user']}@{organization_email_domain}"
            print(f"Owner Email: {owner_email}")
            print(f"Source Email: {source_email}")

            if source_email == owner_email:
                print(f"Sender is allowed, same as recipient")
                return index_email(email, source_email, ses_notification)

            # Check if the soource email is in the settings.allowedSenders list
            elif settings and check_allowed_senders(settings.get('allowedSenders', []), source_email):
                print(f"Sender is allowed by settings")
                return index_email(email, source_email, ses_notification)

            else:
                print(f"Sender is not allowed")
                return {'disposition': 'STOP_RULE_SET'}

        return None
