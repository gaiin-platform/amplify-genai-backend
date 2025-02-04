import json

from events.event_handler import MessageHandler
from service.handlers import handle_event


from typing import Dict, Any, List

from events.email_events import SESMessageHandler

_handlers: List[MessageHandler] = []

def register_handler(handler: MessageHandler):
    _handlers.append(handler)


def route_queue_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process messages from SQS queue by trying registered handlers."""
    print(f"Processing SQS event: {json.dumps(event)}")

    for record in event.get('Records', []):
        try:
            message_body = json.loads(record.get('body', '{}'))

            print("Starting handler chain...")
            for handler in _handlers:
                if handler.can_handle(message_body):
                    print("Found handler to process message.")
                    agent_input_event = handler.process(message_body, context)

                    if agent_input_event:
                        # print(f"Agent input event: {agent_input_event}")
                        response = process_and_invoke_agent(agent_input_event)
                        print("Agent response:", response)
                    else:
                        print(f"Ignoring event per handler instructions (e.g., return None)")

        except Exception as e:
            print(f"Error processing message: {e}")
            raise

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Successfully processed events'})
    }


def process_and_invoke_agent(event: dict):
    """
    Processes an event, ensures it follows the expected format, and invokes `handlers.handle_event`.

    Args:
        event (dict): The event dictionary containing user session details, prompt, metadata, and files.

    Returns:
        dict: The response from `handlers.handle_event`.
    """
    try:
        # Extract required fields
        current_user = event.get("currentUser")
        session_id = event.get("sessionId")
        prompt = event.get("prompt", [])
        metadata = event.get("metadata", {})

        # Validate required fields
        if not current_user or not session_id or not prompt:
            raise ValueError("Missing required fields: currentUser, sessionId, or prompt")

        # Invoke the event handler
        response = handle_event(
            current_user=current_user,
            access_token=metadata.get("accessToken"),  # Optional, if needed
            session_id=session_id,
            prompt=prompt,
            metadata=metadata
        )

        return response

    except Exception as e:
        return {
            "handled": False,
            "error": str(e)
        }


register_handler(SESMessageHandler())