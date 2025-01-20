import json
import os
import traceback

from agent.agents import actions_agent
from agent.game.action import ActionRegistry
from agent.game.agent_registry import AgentRegistry
from agent.game.environment import Environment
from agent.prompt import create_llm
from common.ops import vop
import boto3
from typing import Dict, Any
from datetime import datetime

import boto3
import json
from datetime import datetime
import os
from typing import List, Dict, Any
from botocore.exceptions import ClientError

def save_conversation_state(current_user: str, session_id: str, conversation_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Saves conversation results to S3 and updates the DynamoDB record with the S3 location.

    Args:
        current_user: The user identifier
        session_id: The session identifier
        conversation_results: The conversation results to store

    Returns:
        Dict containing status and any error information
    """
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.getenv('AGENT_STATE_DYNAMODB_TABLE'))

        # Get bucket name from environment
        bucket = os.getenv('AGENT_STATE_BUCKET')
        if not bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        # Generate date string for the folder structure
        current_date = datetime.utcnow().strftime('%Y-%m-%d')

        # Construct S3 key
        s3_key = f"{current_user}/{current_date}/{session_id}.json"

        # Convert conversation results to JSON and store in S3
        try:
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=json.dumps(conversation_results, indent=2),
                ContentType='application/json'
            )
        except ClientError as e:
            print(f"Error storing conversation in S3: {e}")
            return {
                "success": False,
                "error": "Failed to store conversation in S3",
                "details": str(e)
            }

        # Update DynamoDB with S3 location
        try:
            table.update_item(
                Key={
                    'user': current_user,
                    'sessionId': session_id
                },
                UpdateExpression='SET memory = :memory, lastUpdated = :timestamp',
                ExpressionAttributeValues={
                    ':memory': {
                        'bucket': bucket,
                        'key': s3_key,
                        'lastModified': datetime.utcnow().isoformat()
                    },
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
        except ClientError as e:
            print(f"Error updating DynamoDB record: {e}")
            return {
                "success": False,
                "error": "Failed to update DynamoDB record",
                "details": str(e)
            }

        return {
            "success": True,
            "s3_location": {
                "bucket": bucket,
                "key": s3_key
            }
        }

    except Exception as e:
        print(f"Unexpected error in save_conversation_state: {e}")
        return {
            "success": False,
            "error": "Unexpected error occurred",
            "details": str(e)
        }

def event_printer(event_id: str, event: Dict[str, Any], current_user: str, session_id: str):
    """
    Prints events and stores agent responses in DynamoDB.

    Args:
        event_id: The type of event
        event: The event data dictionary
        current_user: The user hash key for DynamoDB
        session_id: The session range key for DynamoDB
    """
    # Print event info
    context_id_prefix = event.get("context_id", "na")
    correlation_id = event.get("correlation_id", None)

    if correlation_id:
        context_id_prefix = f"{context_id_prefix}/{correlation_id}"

    print(f"{context_id_prefix} Event: {event_id} - {event}")

    # Store agent responses in DynamoDB
    if event_id == "agent/prompt/action/raw_result":
        print("  Agent Response:")
        print(event["response"])

        # Initialize DynamoDB client
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.getenv('AGENT_STATE_DYNAMODB_TABLE'))

        # Prepare item for DynamoDB
        item = {
            'user': current_user,
            'sessionId': session_id,
            'state': event["response"],
            'timestamp': datetime.utcnow().isoformat(),
            'eventId': event_id
        }

        # Add context and correlation IDs if present
        if context_id_prefix != "na":
            item['contextId'] = context_id_prefix
        if correlation_id:
            item['correlationId'] = correlation_id

        try:
            # Write to DynamoDB
            table.put_item(Item=item)
            print(f"Stored agent response in DynamoDB for user {current_user}, session {session_id}")
        except Exception as e:
            print(f"Error storing agent response in DynamoDB: {e}")

@vop(
    path="/vu-agent/handle-event",
    tags=["default"],
    name="agentHandleEvent",
    description="Trigger an agent to handle an event.",
    params={
        "sessionId": "The session ID.",
        "eventType": "The name of the event type.",
        "eventData": "The data for the event.",
    },
    schema={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string"},
            "prompt": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["prompt", "sessionId"],
    }
)
def handle_event(current_user, access_token, session_id, prompt, metadata=None):
    print(f"[{session_id}] Handling event '{prompt}'")

    try:
        metadata = metadata or {}
        environment = Environment()
        action_registry = ActionRegistry()

        llm = create_llm(access_token, metadata.get('model', os.getenv("AGENT_MODEL")))

        agent = actions_agent.build(environment, action_registry, llm)

        agent_registry = AgentRegistry()
        agent_registry.register("Action Agent", "Can use tools to take actions on behalf of the user.", agent)

        # Create a wrapper function to pass additional parameters to event_printer
        def event_printer_wrapper(event_id: str, event: Dict[str, Any]):
            return event_printer(event_id, event, current_user, session_id)

        action_context_props={
            "event_handler": event_printer_wrapper,
            "agent_registry": agent_registry,
            "llm": llm,
        }

        user_input = prompt

        result = agent.run(user_input=user_input, action_context_props=action_context_props)

        def load_memory_content(memory):
            content = memory['content']
            try:
                return json.loads(content)
            except:
                return content

        # Convert memory to a list of dicts
        processed_result = [
            {
             "role":item['type'],
             "content": load_memory_content(item)
             }
            for item in result.items]

        # Save conversation state to S3 and update DynamoDB
        save_result = save_conversation_state(current_user, session_id, processed_result)

        if not save_result["success"]:
            print(f"Warning: Failed to save conversation state: {save_result['error']}")


        return {
            "handled": True,
            "result": processed_result
        }
    except Exception as e:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {e}")
        return {
            "handled": False,
            "error": "Error handling event"
        }