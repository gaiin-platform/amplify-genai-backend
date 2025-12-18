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
            # First check if it's a valid SES message
            if not is_ses_message(message):
                return False

            # Check if destination email is notes@vanderbilt.ai
            destination_emails = extract_destination_emails(message)
            if self.NOTES_EMAIL in destination_emails:
                logger.info("Notes email detected: %s", self.NOTES_EMAIL)
                return True

            return False

        except Exception as e:
            logger.error("Error in SESNotesMessageHandler.can_handle: %s", e)
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

            logger.info("Notes email from: %s to: %s", source_email, destination_emails)

            # Extract email body and attachments
            parsed_email = extract_email_body_and_attachments(ses_content)
            email_body = parsed_email.get("body_plain") or parsed_email.get("body_html", "")

            # Determine the user from the sender's email
            sender_username = lookup_username_from_email(source_email)

            ### PROCESSING LOGIC  ###
            # Forward to Notes Ingest Queue for processing
            import os
            import boto3

            notes_queue_url = os.getenv("NOTES_INGEST_QUEUE_URL")
            if not notes_queue_url:
                logger.error("NOTES_INGEST_QUEUE_URL environment variable not set")
                return {"result": None}

            try:
                # Get subject
                subject = common_headers.get("subject", "No Subject")

                # Prepare message for Notes app
                notes_message = {
                    "sender": source_email,
                    "username": sender_username,
                    "subject": subject,
                    "body": email_body,
                    "attachments": [
                        {
                            "filename": att["filename"],
                            "content_type": att["content_type"],
                            "size": len(att["content"])
                        }
                        for att in parsed_email.get("attachments", [])
                    ],
                    "raw_email_s3": {
                        "bucket": ses_content.get("receipt", {}).get("action", {}).get("bucketName"),
                        "key": ses_content.get("receipt", {}).get("action", {}).get("objectKey")
                    },
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
