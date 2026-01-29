import json
import os
from events.event_handler import MessageHandler
from service.handlers import handle_event
import boto3

from typing import Dict, Any, List

from events.email_events import SESMessageHandler
from scheduled_tasks_events.scheduled_tasks import TasksMessageHandler

from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH
logger = getLogger("agent_queue")

sqs = boto3.client("sqs")
agent_queue = os.environ["AGENT_QUEUE_URL"]

_handlers: List[MessageHandler] = []


def register_handler(handler: MessageHandler):
    _handlers.append(handler)


@required_env_vars({
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution(operation_name="route_queue_event", account="system")
def route_queue_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process messages from SQS queue by trying registered handlers."""
    logger.info(f"Processing SQS event: {json.dumps(event)}")

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
                        logger.info(f"Agent response: {response}")

                        if response.get("handled"):
                            # delete record from sqs
                            try:
                                sqs.delete_message(
                                    QueueUrl=agent_queue, ReceiptHandle=receipt_handle
                                )
                            except Exception as e:
                                logger.warning(f"Error deleting message: {e}, continuing")

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
                                f"Final agent results: {json.dumps(result, separators=(',', ':'))}"
                            )
                            handler.onSuccess(agent_input_event, result)
                        else:
                            # Handle case when agent returns handled=False
                            error_msg = response.get("error", "Agent failed to handle event")
                            logger.error(f"Agent failed to handle event: {error_msg}")
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
            logger.error(f"Error processing message: {e}")
            
            # CRITICAL: Message processing failure = agent workflow blocked
            
            import traceback
            log_critical_error(
                function_name="process_queue_messages",
                error_type="AgentQueueMessageProcessingFailure",
                error_message=f"Failed to process SQS message: {str(e)}",
                severity=SEVERITY_HIGH,
                stack_trace=traceback.format_exc(),
                context={
                    "message_id": record.get('messageId'),
                    "receipt_handle": receipt_handle[:50] if receipt_handle else 'unknown',
                    "error_details": str(e)
                }
            )
            
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
                logger.warning(f"Error changing message visibility: {e}, continuing")
            raise

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Successfully processed events"}),
    }


def process_and_invoke_agent(event: dict):
    """
    Processes an event, ensures it follows the expected format, and invokes the agent handler.

    Args:
        event (dict): The event dictionary containing user session details, prompt, metadata, and files.

    Returns:
        dict: The response from the agent handler.
    """
    try:
        logger.info(f"Processing event prompt: {event.get('prompt')}")
        logger.info(f"Processing event metadata: {event.get('metadata')}")

        # Extract required fields
        current_user = event.get("currentUser")
        session_id = event.get("sessionId")
        prompt = event.get("prompt", [])
        metadata = event.get("metadata", {})
        apiKey = metadata.get("accessToken")

        if not apiKey:
            raise ValueError("Missing required field: apiKey")

        # Validate required fields
        if not current_user or not session_id:
            raise ValueError("Missing required fields: currentUser or sessionId")

        # Direct function call to handle_event
        logger.info("Invoking agent handler directly")
        response = handle_event(
            current_user=current_user,
            access_token=apiKey,
            session_id=session_id,
            prompt=prompt,
            metadata=metadata,
        )
        return response

    except Exception as e:
        logger.error(f"Error processing event: {e}")

        # CRITICAL: General agent invocation failure
        import traceback
        log_critical_error(
            function_name="process_and_invoke_agent",
            error_type="AgentInvocationFailure",
            error_message=f"Failed to invoke agent: {str(e)}",
            current_user=event.get("currentUser"),
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc(),
            context={
                "session_id": event.get("sessionId"),
                "has_prompt": bool(event.get("prompt")),
                "error_details": str(e)
            }
        )

        return {"handled": False, "error": str(e)}


register_handler(SESMessageHandler())
register_handler(TasksMessageHandler())
