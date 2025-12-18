
import json
from typing import Dict, Any
from events.event_handler import MessageHandler, SPECIALIZED_EMAILS
from events.ses_message_functions import (
    extract_email_body_and_attachments,
    lookup_username_from_email,
    extract_destination_emails,
    is_ses_message
)
from pycommon.logger import getLogger
logger = getLogger("scheduling_email_events")


class SESSchedulingMessageHandler(MessageHandler):
    """Handler for emails sent to schedule@vanderbilt.edu"""

    # Get scheduler email from registry (single source of truth)
    SCHEDULER_EMAIL = SPECIALIZED_EMAILS["SCHEDULER"]

    def is_agent_loop_event(self, event: Dict[str, Any]) -> bool:
        """Scheduling events should not trigger agent loop execution"""
        return False

    def can_handle(self, message: Dict[str, Any]) -> bool:
        """Check if message is an SES event sent to the scheduler email"""
        try:
            # First check if it's a valid SES message
            if not is_ses_message(message):
                return False

            # Check if destination email is schedule@vanderbilt.edu
            destination_emails = extract_destination_emails(message)
            if self.SCHEDULER_EMAIL in destination_emails:
                logger.info("Scheduling email detected: %s", self.SCHEDULER_EMAIL)
                return True

            return False

        except Exception as e:
            logger.error("Error in SESSchedulingMessageHandler.can_handle: %s", e)
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Process scheduling email and create agent event"""
        logger.info("Processing scheduling email")

        try:
            ses_content = json.loads(message["Message"])
            mail_data = ses_content["mail"]
            common_headers = mail_data.get("commonHeaders", {})

            # Extract sender and recipients
            source_email = mail_data["source"].lower()
            destination_emails = mail_data["destination"]

            logger.info("Scheduling email from: %s to: %s", source_email, destination_emails)

            # Extract email body and attachments
            parsed_email = extract_email_body_and_attachments(ses_content)
            email_body = parsed_email.get("body_plain") or parsed_email.get("body_html", "")

            # Determine the user from the sender's email
            sender_username = lookup_username_from_email(source_email)

            ### PROCESSING LOGIC  ###

            logger.info("Created scheduling agent event for user: %s", sender_username)

            # return must contain result 
            return {"result" : None}

        except Exception as e:
            logger.error("Error processing scheduling email: %s", e, exc_info=True)
            raise

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        logger.error("SESSchedulingMessageHandler onFailure: %s", error)
        pass

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        """Handle successful scheduling event processing"""
        logger.info("Scheduling email processed successfully")

    