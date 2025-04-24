import json
import os
from events.event_handler import MessageHandler
from service.conversations import register_agent_conversation
from service.handlers import handle_event
import boto3

from typing import Dict, Any, List

from events.email_events import SESMessageHandler

sqs = boto3.client('sqs')
agent_queue = os.environ['AGENT_QUEUE_URL']

_handlers: List[MessageHandler] = []

def register_handler(handler: MessageHandler):
    _handlers.append(handler)


def route_queue_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process messages from SQS queue by trying registered handlers."""
    print(f"Processing SQS event: {json.dumps(event)}")

    for record in event.get('Records', []):
        receipt_handle = record.get('receiptHandle')
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


                        if response.get("handled"):
                            #delete record from sqs 
                            if isinstance(handler, SESMessageHandler):
                                try:
                                    sqs.delete_message(
                                        QueueUrl=agent_queue,
                                        ReceiptHandle=receipt_handle
                                    )
                                except Exception as e:
                                    print(f"Error deleting message: {e}, continuing...")

                                        
                            result = response.get("result")
                            if not result:
                                print(f"Agent response missing")
                                result = [{"role":"environment", "content":"Failed to run the agent."}]
                      
                            print(f"Registering agent conversation")
                            register_agent_conversation( 
                                access_token=agent_input_event.get("metadata", {}).get("accessToken"),
                                input=agent_input_event,
                                result=result)
                    else:
                        print(f"Ignoring event per handler instructions (e.g., return None)")

        except Exception as e:
            print(f"Error processing message: {e}")
            if isinstance(handler, SESMessageHandler):
                try:
                    sqs.change_message_visibility(
                        QueueUrl=agent_queue,
                        ReceiptHandle=receipt_handle,
                        VisibilityTimeout=0
                    )
                except Exception as e:
                    print(f"Error changing message visibility: {e}, continuing...")
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

        print("Processing event metadata: ", event.get("metadata"))
        # Extract required fields
        current_user = event.get("currentUser")
        session_id = event.get("sessionId")
        prompt = event.get("prompt", [])
        metadata = event.get("metadata", {})

        if not prompt:
            prompt = [{
                "role": "user",
                "content": "I have received an email from ${sender} at ${timestamp}. The subject of the email is: '${subject}'. The email was sent to: ${recipients}. The contents of the email are:\n\n'''${contents}'''"
            }]

        # Validate required fields
        if not current_user or not session_id:
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