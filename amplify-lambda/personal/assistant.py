import hashlib
import json
import re
import os
from datetime import datetime
import base64
import email
from email import policy
import boto3
from botocore.exceptions import ClientError

personal_assistant_email_bucket = os.environ['S3_PERSONAL_ASSISTANT_EMAIL_BUCKET_NAME']
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

    return f"{email}/{tag}/{dt_string}/{email_id}"


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
        elif content_type == "text/html" and body_html is None:    # HTML body
            body_html = part.get_payload(decode=True)

    # Return the extracted content
    return {
        "body_plain": body_plain.decode('utf-8') if body_plain else None,
        "body_html": body_html.decode('utf-8') if body_html else None,
        "attachments": attachments
    }


def sanitize_filename(filename):
    """Sanitize the filename to be safe for S3 keys."""
    # Replace ".." with ".", remove leading/trailing periods or slashes
    sanitized = re.sub(r'\.{2,}', '.', filename).strip(".")
    # Replace any remaining special characters with an underscore
    sanitized = re.sub(r'[^\w\-_\.]', '_', sanitized)
    return sanitized


def save_email_to_s3(email_details, bucket_name, base_prefix):
    # Create an S3 client
    s3 = boto3.client('s3')

    email_content = email_details['contents']
    # Determine the body content to save (1)
    body = email_content['body_plain'] if email_content['body_plain'] else email_content['body_html']

    # Construct the body file key
    body_file_name = "email.json"
    body_key = f"{base_prefix}_{body_file_name}"

    email_to_save = {
        "body": body,
        "key_base": base_prefix,
        "timestamp": email_details['timestamp'],
        "subject": email_details['subject'],
        "sender": email_details['sender'],
        "recipients": email_details['recipients'],
        "attachment_file_names": [sanitize_filename(attachment['filename'])
                                  for attachment in email_content['attachments']]
    }

    # Check if the target key already exists and just return True if it does
    try:
        s3.head_object(Bucket=bucket_name, Key=body_key)
        print(f"Email already exists in s3://{bucket_name}/{body_key}")
        return True
    except ClientError:
        pass

    # Save the body content to S3
    s3.put_object(Bucket=bucket_name, Key=body_key, Body=json.dumps(email_to_save).encode('utf-8'))

    print(f"Saved email body to s3://{bucket_name}/{body_key}")

    # Loop through and save all attachments (2)
    for attachment in email_content['attachments']:
        # Each attachment has a 'filename' and 'content'
        file_name = attachment['filename']
        file_name = sanitize_filename(file_name)
        file_content = attachment['content']

        # Construct the file key
        file_key = f"{base_prefix}_{file_name}"

        # Save the file to S3
        s3.put_object(Bucket=bucket_name, Key=file_key, Body=file_content)

        print(f"Saved attachment to s3://{bucket_name}/{file_key}")


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
    tag = parsed_destination_email['tag'] if parsed_destination_email['tag'] else 'default'

    print(f"Email ID: {email_id}")
    s3_key = get_target_s3_key_base(user_email, tag, email_id)
    print(f"S3 Key Base: {s3_key}")

    # Save the email to S3
    save_email_to_s3(email_details, personal_assistant_email_bucket, s3_key)
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




