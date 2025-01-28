import json
import os
import traceback

import boto3

# Need to stay for the action registry to discover tools
import agent.tools.file_handling
import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec
import agent.tools.http_requests
import agent.tools.prompt_tools

from agent.agents import actions_agent
from agent.game.action import ActionRegistry, Action
from agent.game.agent_registry import AgentRegistry
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.prompt import create_llm
from agent.tools.ops import ops_to_tools
from common.ops import vop
from datetime import datetime
from typing import List, Dict, Any
from botocore.exceptions import ClientError

from service.session_files import create_file_tracker


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

        # Construct S3 key
        s3_key = f"{current_user}/{session_id}/agent_state.json"

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
    print(f"[{session_id}] Handling event")

    try:
        work_directory = get_working_directory(session_id)

        tracker = create_file_tracker(current_user, session_id, work_directory)

        metadata = metadata or {}
        agent_id = "default"

        additional_goals = [
            Goal(
                name="Work Directory",
                description=f"Any files you would like to save/write MUST be saved in {work_directory}. It is the only writable directory."
            )
        ]

        if 'assistant' in metadata:
            assistant = metadata['assistant']
            print(f"Assistant metadata: {json.dumps(metadata['assistant'])}")
            if assistant['instructions']:
                print(f"Adding assistant instructions to goals: {assistant['instructions']}")
                additional_goals.append(Goal(
                    name="What to Do",
                    description=assistant['instructions']
                ))

        model = metadata.get('agent_model', os.getenv("AGENT_MODEL"))

        environment = Environment()
        action_registry = ActionRegistry()

        # op_tools = ops_to_tools(action_context)
        # for op_tool in op_tools:
        #     action_registry.register_action(
        #         Action(
        #             name=op_tool['tool_name'],
        #             function=op_tool["function"],
        #             description=op_tool["description"],
        #             args=op_tool.get("args",{}),
        #             output=op_tool.get("output", {}),
        #             terminal=op_tool.get("terminal", False)
        #         )
        #     )


        llm = create_llm(access_token, metadata.get('model', model))

        agent = actions_agent.build(
            environment=environment,
            action_registry=action_registry,
            generate_response=llm,
            additional_goals=additional_goals)

        agent_registry = AgentRegistry()
        agent_registry.register("Action Agent", "Can use tools to take actions on behalf of the user.", agent)

        # Create a wrapper function to pass additional parameters to event_printer
        def event_printer_wrapper(event_id: str, event: Dict[str, Any]):
            return event_printer(event_id, event, current_user, session_id)

        action_context_props={
            'current_user': current_user,
            'access_token': access_token,
            'session_id': session_id,
            'agent_id': agent_id,
            "event_handler": event_printer_wrapper,
            "agent_registry": agent_registry,
            "llm": llm,
            "work_directory": work_directory,
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

        print(f"Conversation state saved to S3: {save_result['s3_location']}")
        print(f"Checking for changed files...")
        file_results = tracker.upload_changed_files()
        session_files = tracker.get_tracked_files()

        return build_response(
            session_id=session_id,
            current_user=current_user,
            processed_result=processed_result,
            file_results=file_results,
            session_files=session_files
        )

    except Exception as e:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {e}")
        return {
            "handled": False,
            "error": "Error handling event"
        }


def get_working_directory(session_id):
    work_directory = os.environ.get("WORK_DIRECTORY", None)
    if not work_directory:
        work_directory = f"/tmp/{session_id}"
        if not os.path.exists(work_directory):
            os.makedirs(work_directory)
    work_directory = os.path.join(work_directory, session_id)
    return work_directory


def generate_file_download_urls(
        current_user: str,
        session_id: str,
        files: Dict[str, Dict],
        expiration: int = 3600
) -> Dict[str, Dict]:
    """
    Generate presigned URLs for downloading files from S3.

    Args:
        current_user (str): The user ID or name
        session_id (str): The session identifier
        files (Dict[str, Dict]): Dictionary of files from handle_event response
        expiration (int): URL expiration time in seconds (default: 1 hour)

    Returns:
        Dict[str, Dict]: Dictionary mapping file IDs to their download information
    """
    try:
        s3_client = boto3.client('s3')
        bucket = os.getenv('AGENT_STATE_BUCKET')

        if not bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        # First get the index file to map original names to S3 keys
        index_key = f"{current_user}/{session_id}/index.json"
        try:
            index_response = s3_client.get_object(
                Bucket=bucket,
                Key=index_key
            )
            index_content = json.loads(index_response['Body'].read().decode('utf-8'))
            filename_mappings = index_content.get('mappings', {})
        except ClientError as e:
            print(f"Error reading index file: {e}")
            return {}

        download_info = {}

        # Generate signed URLs for each file
        for file_id, file_details in files.items():
            original_name = file_details['original_name']

            # Look up the S3 key from mappings
            if original_name not in filename_mappings:
                print(f"Warning: No mapping found for {original_name}")
                continue

            s3_key = f"{current_user}/{session_id}/{filename_mappings[original_name]}"

            try:
                url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': bucket,
                        'Key': s3_key
                    },
                    ExpiresIn=expiration
                )

                download_info[file_id] = {
                    "original_name": original_name,
                    "size": file_details['size'],
                    "last_modified": file_details['last_modified'],
                    "download_url": url,
                    "expires_in": expiration
                }

            except ClientError as e:
                print(f"Error generating presigned URL for {original_name}: {e}")
                continue

        return download_info

    except Exception as e:
        print(f"Error generating download URLs: {e}")
        return {}



def build_response(
        session_id: str,
        current_user: str,
        processed_result: List,
        file_results: Dict = None,
        session_files: Dict = None
) -> Dict:
    """
    Build a standardized response including file download URLs.

    Args:
        session_id (str): The session identifier
        current_user (str): The user ID or name
        processed_result (List): The processed conversation result
        file_results (Dict, optional): Results from file uploads
        session_files (Dict, optional): Dictionary of tracked files

    Returns:
        Dict: Standardized response with file information and download URLs
    """
    print(f"Building response for session {session_id}")
    response = {
        "session": session_id,
        "handled": True,
        "result": processed_result
    }

    if session_files:
        print("Creating download URLs for session files")
        print(f"Session files: {session_files}")

        # Generate download URLs for all files
        download_info = generate_file_download_urls(
            current_user=current_user,
            session_id=session_id,
            files=session_files
        )

        response["files"] = session_files
        response["downloads"] = download_info

    if file_results and file_results["status"] == "success" and file_results["files_processed"] > 0:
        response["changed_files"] = file_results["changed_files"]

    return response