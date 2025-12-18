from abc import ABC, abstractmethod
from typing import Dict, Any
from pycommon.logger import getLogger

logger = getLogger("event_handler")

"""

Process should return a dictionary with the following structure:

{
    "currentUser": str,  # Current user identifier
    "sessionId": str,  # Unique identifier for the session
    "is_agent_loop_event": bool,  # Whether this event should trigger an agent loop execution
    "prompt": [
        {
            "role": str,  # "user" or "assistant"
            "content": str  # The actual message content
        }
    ],
    "metadata": {  # Open-ended dictionary for flexibility
        "source": str,  # Identifier of the event source (e.g., "SES", "SQS", "API Gateway", etc.)
        "eventId": str,  # Unique ID of the event (if applicable)
        "timestamp": str,  # ISO 8601 timestamp of the event
        "assistant": {  # Optional assistant instructions for processing
            "instructions": str,  # Special processing instructions
            "data": {
                "operations": [
                    {
                        "name": str,  # Name of the tool to register
                        "description": str,  # Description of the tool
                        "parameters": dict,  # Input parameters for the tool
                        "output": dict,  # Expected output format (optional)
                        "terminal": bool  # If the tool is a final action (optional)
                        "tags": list  # List of tags for the tool (optional)
                    }
                ]
            }
        },
        "files": [  # Optional: List of file references related to this event
            {
                "fileId": str,  # Unique file identifier
                "originalName": str,  # Original name of the file
                "downloadUrl": str,  # Presigned S3 URL or another retrievable location
                "size": int,  # File size in bytes (optional)
            }
        ]
    }
}
"""
class MessageHandler(ABC):

    @abstractmethod
    def can_handle(self, event: Dict[str, Any]) -> bool:
        pass

    @abstractmethod
    def is_agent_loop_event(self) -> bool:
        pass

    @abstractmethod
    def process(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        pass

    @abstractmethod
    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        pass

    @abstractmethod
    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        pass


# ============================================================================
# Email Handler Registry - Single source of truth for specialized email handlers
# ============================================================================
"""
Registry of specialized email addresses that are handled by specific handlers
(not the general SESMessageHandler).

When adding a new specialized email handler:
1. Add the email address to SPECIALIZED_EMAILS with a descriptive key
2. Create the handler class that imports and uses SPECIALIZED_EMAILS
3. The general SESMessageHandler will automatically exclude it

Example:
    from events.event_handler import SPECIALIZED_EMAILS

    class SESSchedulingMessageHandler(MessageHandler):
        SCHEDULER_EMAIL = SPECIALIZED_EMAILS["SCHEDULER"]
"""

# Registry of specialized email addresses
# Key: Descriptive constant name (use in handlers)
# Value: Email address
SPECIALIZED_EMAILS = {
    "SCHEDULER": "schedule@vanderbilt.edu",
    "NOTES": "notes@dev.vanderbilt.ai",
}


def get_specialized_emails():
    """
    Get list of email addresses handled by specialized handlers.

    Returns:
        list: List of email addresses (lowercase)
    """
    return list(SPECIALIZED_EMAILS.values())


def is_specialized_email(email):
    """
    Check if an email address is handled by a specialized handler.

    Args:
        email (str): Email address to check

    Returns:
        bool: True if handled by specialized handler
    """
    return email.lower() in [e.lower() for e in SPECIALIZED_EMAILS.values()]
