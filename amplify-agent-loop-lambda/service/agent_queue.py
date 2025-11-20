import json
import os
import requests
from events.event_handler import MessageHandler
from service.handlers import handle_event
import boto3

from typing import Dict, Any, List

from events.email_events import SESMessageHandler
from scheduled_tasks_events.scheduled_tasks import TasksMessageHandler

from pycommon.logger import getLogger
logger = getLogger("agent_queue")

sqs = boto3.client("sqs")
agent_queue = os.environ["AGENT_QUEUE_URL"]
agent_fat_container_url = os.environ.get("AGENT_FAT_CONTAINER_URL")

_handlers: List[MessageHandler] = []


def register_handler(handler: MessageHandler):
    _handlers.append(handler)


def route_queue_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process messages from SQS queue by trying registered handlers."""
    logger.info("Processing SQS event: %s", json.dumps(event))

    for record in event.get("Records", []):
        receipt_handle = record.get("receiptHandle")
        try:
            message_body = json.loads(record.get("body", "{}"))

            logger.info("Starting handler chain")
            for handler in _handlers:
                if handler.can_handle(message_body):
                    logger.info("Found handler to process message")
                    agent_input_event = handler.process(message_body, context)

                    if agent_input_event:
                        # print(f"Agent input event: {agent_input_event}")
                        response = process_and_invoke_agent(agent_input_event)
                        logger.info("Agent response: %s", response)

                        if response.get("handled"):
                            # delete record from sqs
                            try:
                                sqs.delete_message(
                                    QueueUrl=agent_queue, ReceiptHandle=receipt_handle
                                )
                            except Exception as e:
                                logger.warning("Error deleting message: %s, continuing", e)

                            result = response.get("result")
                            if not result:
                                logger.error("Agent response missing")
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
                            logger.info(
                                "Final agent results: %s", json.dumps(result, separators=(',', ':'))
                            )
                            handler.onSuccess(agent_input_event, result)
                        else:
                            # Handle case when fat container returns handled=False
                            error_msg = response.get("error", "Agent failed to handle event")
                            logger.error("Agent failed to handle event: %s", error_msg)
                            handler.onFailure(
                                agent_input_event,
                                Exception(f"Agent failed to handle event: {error_msg}")
                            )
                    else:
                        # If agent_input_event is None, pass the original message_body instead
                        event_for_failure = agent_input_event if agent_input_event is not None else message_body
                        handler.onFailure(
                            event_for_failure, Exception("No result from handler")
                        )
                        logger.info(
                            "Ignoring event per handler instructions (e.g., return None)"
                        )

        except Exception as e:
            logger.error("Error processing message: %s", e)
            # Only call onFailure if we have a handler that can process this message
            for handler in _handlers:
                if handler.can_handle(message_body):
                    handler.onFailure(message_body, e)
                    break
            try:
                sqs.change_message_visibility(
                    QueueUrl=agent_queue,
                    ReceiptHandle=receipt_handle,
                    VisibilityTimeout=0,
                )
            except Exception as e:
                logger.warning("Error changing message visibility: %s, continuing", e)
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
        logger.info("Processing event prompt: %s", event.get("prompt"))
        logger.info("Processing event metadata: %s", event.get("metadata"))
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
            logger.info("Calling fat container at: %s", endpoint_url)
            
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

            logger.warning("Fat container returned status %d: %s", response.status_code, response.text)
            return {"handled": False, "error": f"HTTP {response.status_code}: {response.text}"}
        else:
            # Fallback to direct function call if URL not available
            logger.info("Fat container URL not found, falling back to direct function call")
            response = handle_event(
                current_user=current_user,
                access_token=apiKey,
                session_id=session_id,
                prompt=prompt,
                metadata=metadata,
            )
            return response

    except requests.exceptions.RequestException as e:
        logger.error("HTTP request failed: %s", e)
        return {"handled": False, "error": f"Request failed: {str(e)}"}
    except Exception as e:
        logger.error("Error processing event: %s", e)
        return {"handled": False, "error": str(e)}


register_handler(SESMessageHandler())
register_handler(TasksMessageHandler())
