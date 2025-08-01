import json
import os
import requests
from events.event_handler import MessageHandler
from service.handlers import handle_event
import boto3

from typing import Dict, Any, List

from events.email_events import SESMessageHandler
from scheduled_tasks_events.scheduled_tasks import TasksMessageHandler

sqs = boto3.client("sqs")
agent_queue = os.environ["AGENT_QUEUE_URL"]
agent_fat_container_url = os.environ.get("AGENT_FAT_CONTAINER_URL")

_handlers: List[MessageHandler] = []


def register_handler(handler: MessageHandler):
    _handlers.append(handler)


def route_queue_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process messages from SQS queue by trying registered handlers."""
    print(f"Processing SQS event: {json.dumps(event)}")

    for record in event.get("Records", []):
        receipt_handle = record.get("receiptHandle")
        try:
            message_body = json.loads(record.get("body", "{}"))

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
                            # delete record from sqs
                            try:
                                sqs.delete_message(
                                    QueueUrl=agent_queue, ReceiptHandle=receipt_handle
                                )
                            except Exception as e:
                                print(f"Error deleting message: {e}, continuing...")

                            result = response.get("result")
                            if not result:
                                print(f"Agent response missing")
                                handler.onFailure(
                                    agent_input_event,
                                    Exception(
                                        "Failed to run the agent: Agent response missing"
                                    ),
                                )
                                result = [
                                    {
                                        "role": "environment",
                                        "content": "Failed to run the agent.",
                                    }
                                ]
                            print(
                                f"Final agent results: {json.dumps(result, separators=(',', ':'))}"
                            )
                            handler.onSuccess(agent_input_event, result)
                    else:
                        handler.onFailure(
                            agent_input_event, Exception("No result from handler")
                        )
                        print(
                            f"Ignoring event per handler instructions (e.g., return None)"
                        )

        except Exception as e:
            print(f"Error processing message: {e}")
            handler.onFailure(message_body, e)
            try:
                sqs.change_message_visibility(
                    QueueUrl=agent_queue,
                    ReceiptHandle=receipt_handle,
                    VisibilityTimeout=0,
                )
            except Exception as e:
                print(f"Error changing message visibility: {e}, continuing...")
            raise

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Successfully processed events"}),
    }


def process_and_invoke_agent(event: dict):
    """
    Processes an event, ensures it follows the expected format, and invokes the fat container.

    Args:
        event (dict): The event dictionary containing user session details, prompt, metadata, and files.

    Returns:
        dict: The response from the fat container.
    """
    try:
        print("Processing event prompt: ", event.get("prompt"))
        print("Processing event metadata: ", event.get("metadata"))
        # Extract required fields
        current_user = event.get("currentUser")
        session_id = event.get("sessionId")
        prompt = event.get("prompt", [])
        metadata = event.get("metadata", {})
        apiKey = metadata.get("accessToken")
        if not apiKey:
            raise ValueError("Missing required fields: apiKey")

        # Validate required fields
        if not current_user or not session_id:
            raise ValueError(
                "Missing required fields: currentUser, sessionId, or prompt"
            )

        # Prepare the request payload for the fat container
        payload = {
            "currentUser": current_user,
            "sessionId": session_id,
            "prompt": prompt,
            "metadata": metadata
        }

        # Make HTTP call to fat container
        if agent_fat_container_url:
            endpoint_url = f"{agent_fat_container_url.rstrip('/')}/vu-agent/handle-event"
            print(f"Calling fat container at: {endpoint_url}")
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {apiKey}"
            }
            
            response = requests.post(
                endpoint_url,
                json={"data": payload},
                headers=headers,
                timeout=890  # Slightly less than Lambda timeout
            )
            
            if response.status_code == 200:
                resp = response.json()
                return resp.get("data", {"handled": False})

            print(f"Fat container returned status {response.status_code}: {response.text}")
            return {"handled": False, "error": f"HTTP {response.status_code}: {response.text}"}
        else:
            # Fallback to direct function call if URL not available
            print("Fat container URL not found, falling back to direct function call")
            response = handle_event(
                current_user=current_user,
                access_token=apiKey,
                session_id=session_id,
                prompt=prompt,
                metadata=metadata,
            )
            return response

    except requests.exceptions.RequestException as e:
        print(f"HTTP request failed: {e}")
        return {"handled": False, "error": f"Request failed: {str(e)}"}
    except Exception as e:
        print(f"Error processing event: {e}")
        return {"handled": False, "error": str(e)}


register_handler(SESMessageHandler())
register_handler(TasksMessageHandler())
