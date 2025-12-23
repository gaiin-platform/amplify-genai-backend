from events.event_handler import MessageHandler, SPECIALIZED_EMAILS
from events.ses_message_functions import lookup_username_from_email
from pycommon.logger import getLogger
logger = getLogger("s3_email_note_events")

import json
import os
import boto3
from typing import Dict, Any
from datetime import datetime
import uuid

class S3EmailNotesMessageHandler(MessageHandler):
    """Handler for S3 events triggered by notes@ emails being stored"""

    # Get notes email from registry (single source of truth)
    NOTES_EMAIL = SPECIALIZED_EMAILS["NOTES"]

    def is_agent_loop_event(self) -> bool:
        """Notes events should not trigger agent loop execution"""
        return False

    def can_handle(self, message: Dict[str, Any]) -> bool:
        """Check if message is an S3 event for the raw emails bucket"""
        try:
            logger.info("Checking if message can be handled by S3EmailNotesMessageHandler")

            # Check if this is an S3 event notification from SNS
            if message.get("Type") != "Notification":
                logger.info("Message is not an SNS notification")
                return False

            # Parse the SNS message
            sns_message = json.loads(message.get("Message", "{}"))

            # Check if it contains S3 event records
            if "Records" not in sns_message:
                logger.info("Message does not contain S3 Records")
                return False

            # Check if it's from our raw emails bucket
            for record in sns_message.get("Records", []):
                if record.get("eventSource") == "aws:s3":
                    bucket = record.get("s3", {}).get("bucket", {}).get("name")
                    expected_bucket = os.getenv("RAW_EMAILS_BUCKET")

                    if bucket == expected_bucket:
                        logger.info("S3 email event detected from bucket: %s", bucket)
                        return True

            logger.info("S3 event not from raw emails bucket")
            return False

        except Exception as e:
            logger.error("Error in S3EmailNotesMessageHandler.can_handle: %s", e, exc_info=True)
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Process notes email from S3 event notification"""
        logger.info("Processing S3 email event for notes@")

        try:
            # Parse the SNS message to get S3 event
            sns_message = json.loads(message.get("Message", "{}"))
            s3_record = sns_message["Records"][0]  # Should only be one per email

            # Extract S3 info
            s3_info = s3_record["s3"]
            s3_bucket = s3_info["bucket"]["name"]
            s3_key = s3_info["object"]["key"]
            s3_size = s3_info["object"]["size"]

            logger.info(f"Processing email from S3: s3://{s3_bucket}/{s3_key} ({s3_size} bytes)")

            # Download and parse the email
            s3_client = boto3.client("s3")

            try:
                response = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
                raw_email_bytes = response["Body"].read()
                logger.info(f"Downloaded email: {len(raw_email_bytes)} bytes")

                # Parse the email
                from email import policy
                from email.parser import BytesParser
                email_message = BytesParser(policy=policy.default).parsebytes(raw_email_bytes)

                # Extract email metadata
                from_header = email_message.get("From", "")
                to_header = email_message.get("To", "")
                subject = email_message.get("Subject", "No Subject")
                date_header = email_message.get("Date")

                logger.info(f"Email metadata: From={from_header}, To={to_header}, Subject={subject}")

                # Parse sender email from "Name <email>" format
                import re
                email_match = re.search(r'<(.+?)>', from_header)
                if email_match:
                    source_email = email_match.group(1).lower()
                else:
                    source_email = from_header.lower().strip()

                # Extract body and attachments
                body_plain = None
                body_html = None
                attachments_data = []

                for part in email_message.walk():
                    content_type = part.get_content_type()
                    content_disposition = part.get_content_disposition()

                    if content_disposition == "attachment" or (content_disposition == "inline" and part.get_filename()):
                        # This is an attachment
                        filename = part.get_filename()
                        content = part.get_payload(decode=True)
                        if filename and content:
                            attachments_data.append({
                                "filename": filename,
                                "content": content,
                                "content_type": content_type
                            })
                            logger.info(f"Found attachment: {filename}, {len(content)} bytes")
                    elif content_type == "text/plain" and body_plain is None:
                        body_plain = part.get_payload(decode=True)
                    elif content_type == "text/html" and body_html is None:
                        body_html = part.get_payload(decode=True)

                # Decode body
                email_body = ""
                if body_plain:
                    email_body = body_plain.decode("utf-8", errors="replace")
                elif body_html:
                    email_body = body_html.decode("utf-8", errors="replace")

            except Exception as e:
                logger.error(f"Failed to download or parse email from S3: {e}", exc_info=True)
                return {"result": None}

            # Determine the user from the sender's email
            sender_username = lookup_username_from_email(source_email)

            ### UPLOAD ATTACHMENTS TO NOTES S3 BUCKET ###
            notes_bucket = os.getenv("S3_NOTES_RAW_FILES_BUCKET")
            notes_queue_url = os.getenv("NOTES_INGEST_QUEUE_URL")

            if not notes_queue_url or not notes_bucket:
                logger.error("NOTES_INGEST_QUEUE_URL or S3_NOTES_RAW_FILES_BUCKET not set")
                return {"result": None}

            try:
                s3 = boto3.client("s3")
                attachments_metadata = []

                for att in attachments_data:
                    # Generate unique S3 key
                    attachment_id = str(uuid.uuid4())
                    timestamp_prefix = datetime.utcnow().strftime('%Y/%m/%d')
                    attachment_s3_key = f"email-attachments/{sender_username}/{timestamp_prefix}/{attachment_id}/{att['filename']}"

                    # Upload to Notes S3 bucket
                    try:
                        s3.put_object(
                            Bucket=notes_bucket,
                            Key=attachment_s3_key,
                            Body=att["content"],
                            ContentType=att["content_type"],
                            Metadata={
                                'original_filename': att["filename"],
                                'sender': source_email,
                                'username': sender_username,
                                'upload_source': 'email_handler'
                            }
                        )

                        logger.info(f"Uploaded attachment to S3: {notes_bucket}/{attachment_s3_key}")

                        attachments_metadata.append({
                            "filename": att["filename"],
                            "content_type": att["content_type"],
                            "size": len(att["content"]),
                            "s3_bucket": notes_bucket,
                            "s3_key": attachment_s3_key
                        })
                    except Exception as e:
                        logger.error(f"Failed to upload attachment {att['filename']}: {e}")
                        continue

                # Prepare message for Notes app
                # Use email body as custom prompt (trimmed), or None if empty
                custom_prompt = email_body.strip() if email_body else None

                notes_message = {
                    "sender": source_email,
                    "username": sender_username,
                    "subject": subject,
                    "body": email_body,
                    "custom_prompt": custom_prompt,
                    "attachments": attachments_metadata,
                    "timestamp": date_header or datetime.utcnow().isoformat()
                }

                # Send to Notes Ingest Queue
                sqs = boto3.client("sqs")
                sqs.send_message(
                    QueueUrl=notes_queue_url,
                    MessageBody=json.dumps(notes_message)
                )

                logger.info(f"Forwarded notes email from {source_email} to Notes app queue")
                return {"result": {"forwarded": True, "username": sender_username}}

            except Exception as e:
                logger.error(f"Error processing notes attachments: {e}", exc_info=True)
                return {"result": None}

        except Exception as e:
            logger.error("Error processing S3 email event: %s", e, exc_info=True)
            raise

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        logger.error("S3EmailNotesMessageHandler onFailure: %s", error)
        pass

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        """Handle successful notes event processing"""
        logger.info("S3 email event processed successfully")
