from events.event_handler import MessageHandler, SPECIALIZED_EMAILS, NOTES_ENABLED
from events.ses_message_functions import extract_email_body_and_attachments, is_ses_message, lookup_username_from_email, extract_destination_emails, _html_to_plain_text
from pycommon.logger import getLogger
logger = getLogger("note_email_events")

import json
from typing import Dict, Any

class SESNotesMessageHandler(MessageHandler):
    """Handler for emails sent to notes@vanderbilt.ai"""

    # Get notes email from registry (single source of truth)
    NOTES_EMAIL = SPECIALIZED_EMAILS["NOTES"]

    def is_agent_loop_event(self) -> bool:
        """Notes events should not trigger agent loop execution"""
        return False

    def can_handle(self, message: Dict[str, Any]) -> bool:
        """Check if message is an SES event sent to the notes email"""
        try:
            logger.info("Checking if message can be handled by SESNotesMessageHandler")

            if not NOTES_ENABLED:
                logger.info("Notes email handler is disabled (NOTES_ENABLED=false)")
                return False

            # First check if it's a valid SES message
            if not is_ses_message(message):
                logger.info("Message is not a valid SES message")
                return False

            # Check if destination email is notes@vanderbilt.ai
            destination_emails = extract_destination_emails(message)
            logger.info("Destination emails: %s", destination_emails)

            if self.NOTES_EMAIL in destination_emails:
                logger.info("Notes email detected: %s", self.NOTES_EMAIL)
                return True

            logger.info("Notes email not in destination list. Looking for: %s", self.NOTES_EMAIL)
            return False

        except Exception as e:
            logger.error("Error in SESNotesMessageHandler.can_handle: %s", e, exc_info=True)
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Process notes email by downloading from S3 and extracting attachments"""
        logger.info("Processing notes email")

        try:
            ses_content = json.loads(message["Message"])
            mail_data = ses_content["mail"]
            common_headers = mail_data.get("commonHeaders", {})
            receipt = ses_content.get("receipt", {})

            # Extract basic email info
            source_email = mail_data["source"].lower()
            destination_emails = mail_data["destination"]
            subject = common_headers.get("subject", "No Subject")
            timestamp = mail_data.get("timestamp")

            logger.info("Notes email from: %s to: %s, subject: %s", source_email, destination_emails, subject)

            # NEW: Download email from S3
            # SES stores emails with messageId as the S3 key
            import os
            import boto3

            message_id = mail_data.get("messageId")
            s3_bucket = os.getenv("RAW_EMAILS_BUCKET")
            s3_key = f"emails/{message_id}"

            if not s3_bucket or not message_id:
                logger.error("RAW_EMAILS_BUCKET not configured or messageId missing")
                logger.error("Bucket: %s, MessageId: %s", s3_bucket, message_id)
                return {"result": None}

            logger.info(f"Downloading email from S3: s3://{s3_bucket}/{s3_key}")

            # Download email from S3
            s3_client = boto3.client("s3")

            try:
                response = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
                raw_email_bytes = response["Body"].read()
                logger.info(f"Downloaded email: {len(raw_email_bytes)} bytes")

                # Parse the email
                from email import policy
                from email.parser import BytesParser
                email_message = BytesParser(policy=policy.default).parsebytes(raw_email_bytes)

                # Extract body
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

                # Decode body - prefer plain text; fall back to HTML stripped to plain text
                email_body = ""
                if body_plain:
                    email_body = body_plain.decode("utf-8", errors="replace")
                elif body_html:
                    email_body = _html_to_plain_text(body_html.decode("utf-8", errors="replace"))

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
                import uuid
                from datetime import datetime

                s3 = boto3.client("s3")
                attachments_metadata = []

                for att in attachments_data:
                    # Generate unique S3 key
                    attachment_id = str(uuid.uuid4())
                    timestamp_prefix = datetime.utcnow().strftime('%Y/%m/%d')
                    s3_key = f"email-attachments/{sender_username}/{timestamp_prefix}/{attachment_id}/{att['filename']}"

                    # Upload to Notes S3 bucket
                    try:
                        s3.put_object(
                            Bucket=notes_bucket,
                            Key=s3_key,
                            Body=att["content"],
                            ContentType=att["content_type"],
                            Metadata={
                                'original_filename': att["filename"],
                                'sender': source_email,
                                'username': sender_username,
                                'upload_source': 'email_handler'
                            }
                        )

                        logger.info(f"Uploaded attachment to S3: {notes_bucket}/{s3_key}")

                        attachments_metadata.append({
                            "filename": att["filename"],
                            "content_type": att["content_type"],
                            "size": len(att["content"]),
                            "s3_bucket": notes_bucket,
                            "s3_key": s3_key
                        })
                    except Exception as e:
                        logger.error(f"Failed to upload attachment {att['filename']}: {e}")
                        continue

                # Use the plain-text body as the custom prompt (trimmed), or None if empty
                custom_prompt = email_body.strip() if email_body.strip() else None

                # Prepare message for Notes app
                notes_message = {
                    "sender": source_email,
                    "username": sender_username,
                    "subject": subject,
                    "body": email_body,
                    "custom_prompt": custom_prompt,
                    "attachments": attachments_metadata,
                    "timestamp": timestamp
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
            logger.error("Error processing notes email: %s", e, exc_info=True)
            raise

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        logger.error("SESNotesMessageHandler onFailure: %s", error)
        pass

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        """Handle successful notes event processing"""
        logger.info("Notes email processed successfully")
