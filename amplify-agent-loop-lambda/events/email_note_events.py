from events.event_handler import MessageHandler, SPECIALIZED_EMAILS
from events.ses_message_functions import extract_email_body_and_attachments, is_ses_message, lookup_username_from_email, extract_destination_emails
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
        """Process notes email and create agent event"""
        logger.info("Processing notes email")

        try:
            ses_content = json.loads(message["Message"])
            mail_data = ses_content["mail"]
            common_headers = mail_data.get("commonHeaders", {})

            # Extract sender and recipients
            source_email = mail_data["source"].lower()
            destination_emails = mail_data["destination"]
            subject = common_headers.get("subject", "No Subject")

            logger.info("Notes email from: %s to: %s, subject: %s", source_email, destination_emails, subject)
            logger.info("Raw SNS message size: %d bytes", len(message.get("Message", "")))

            # Extract email body and attachments
            logger.info("About to extract email body and attachments...")
            parsed_email = extract_email_body_and_attachments(ses_content)
            logger.info("Extraction complete. Attachments found: %d", len(parsed_email.get("attachments", [])))
            email_body = parsed_email.get("body_plain") or parsed_email.get("body_html", "")

            # Determine the user from the sender's email
            sender_username = lookup_username_from_email(source_email)

            ### PROCESSING LOGIC  ###
            # Forward to Notes Ingest Queue for processing
            import os
            import boto3

            notes_queue_url = os.getenv("NOTES_INGEST_QUEUE_URL")
            s3_bucket = os.getenv("S3_NOTES_RAW_FILES_BUCKET")

            if not notes_queue_url:
                logger.error("NOTES_INGEST_QUEUE_URL environment variable not set")
                return {"result": None}

            if not s3_bucket:
                logger.error("S3_NOTES_RAW_FILES_BUCKET environment variable not set")
                return {"result": None}

            try:
                # Get subject
                subject = common_headers.get("subject", "No Subject")

                # Upload attachments to S3 and prepare metadata
                s3 = boto3.client("s3")
                attachments_metadata = []

                import uuid
                from datetime import datetime

                for att in parsed_email.get("attachments", []):
                    # Generate unique S3 key for this attachment
                    attachment_id = str(uuid.uuid4())
                    timestamp_prefix = datetime.utcnow().strftime('%Y/%m/%d')
                    s3_key = f"email-attachments/{sender_username}/{timestamp_prefix}/{attachment_id}/{att['filename']}"

                    # Upload to S3
                    try:
                        s3.put_object(
                            Bucket=s3_bucket,
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

                        logger.info(f"Uploaded attachment to S3: {s3_bucket}/{s3_key}")

                        attachments_metadata.append({
                            "filename": att["filename"],
                            "content_type": att["content_type"],
                            "size": len(att["content"]),
                            "s3_bucket": s3_bucket,
                            "s3_key": s3_key
                        })
                    except Exception as e:
                        logger.error(f"Failed to upload attachment {att['filename']} to S3: {e}")
                        # Skip this attachment if upload fails
                        continue

                # Prepare message for Notes app
                notes_message = {
                    "sender": source_email,
                    "username": sender_username,
                    "subject": subject,
                    "body": email_body,
                    "attachments": attachments_metadata,
                    "timestamp": ses_content.get("mail", {}).get("timestamp")
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
                logger.error(f"Error forwarding to Notes app: {e}")
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
