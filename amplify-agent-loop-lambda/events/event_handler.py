from abc import ABC, abstractmethod
from typing import Dict, Any

"""

Process should return a dictionary with the following structure:

{
    "currentUser": str,  # Current user identifier
    "sessionId": str,  # Unique identifier for the session
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
