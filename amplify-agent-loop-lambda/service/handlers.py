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

from service.session_files import create_file_tracker, get_presigned_url_by_id


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
        "prompt": "The prompt for the agent.",
        "metadata": "Additional properties.",
    },
    schema={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string"},
            "prompt": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["role", "content"],
                    "additionalProperties": True
                }
            },
            "metadata": {"type": "object"},
        },
        "required": ["prompt", "sessionId"],
    }
)
def handle_event(current_user, access_token, session_id, prompt, metadata=None):

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
        environment = Environment()
        action_registry = ActionRegistry()

        if 'assistant' in metadata:
            assistant = metadata['assistant']
            print(f"Assistant metadata: {json.dumps(metadata['assistant'])}")
            if assistant['instructions']:
                print(f"Adding assistant instructions to goals: {assistant['instructions']}")
                additional_goals.append(Goal(
                    name="What to Do",
                    description=assistant['instructions']
                ))
            ops = assistant.get("data",{}).get("operations", [])
            print(f"Assistant operations: {ops}")
            op_tools = ops_to_tools(ops)
            for op_tool in op_tools:
                print(f"Registering tool: {op_tool['tool_name']}: {op_tool['description']}")
                action_registry.register(
                    Action(
                        name=op_tool['tool_name'],
                        function=op_tool["function"],
                        description=op_tool["description"],
                        args=op_tool.get("args",{}),
                        output=op_tool.get("output", {}),
                        terminal=op_tool.get("terminal", False)
                    )
                )

        model = metadata.get('agent_model', os.getenv("AGENT_MODEL"))

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

        # Remove any messages from the prompt that aren't user or assistant
        prompt = [entry for entry in prompt if entry['role'] in ['user', 'assistant']]
        # Combine all of the content attributes of the prompt entries into one string separated by newlines and
        # using the template: {role}: {content}
        user_input = "\n".join([f"{entry['role']}: {entry['content']}" for entry in prompt])

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

def build_response(
        session_id: str,
        current_user: str,
        processed_result: List,
        file_results: Dict = None,
        session_files: Dict = None
) -> Dict:
    """
    Build a standardized response including files and their version history.
    """
    print(f"Building response for session {session_id}")
    response = {
        "session": session_id,
        "handled": True,
        "result": processed_result
    }

    if session_files:
        # Transform version info in session_files to use version_file_id
        transformed_files = {}
        for file_id, file_info in session_files.items():
            file_entry = {
                "original_name": file_info["original_name"],
                "size": file_info["size"],
                "last_modified": file_info["last_modified"]
            }

            if "versions" in file_info:
                file_entry["versions"] = [
                    {
                        "version_file_id": v.get("s3_name", "").rsplit('.', 1)[0],
                        "timestamp": v["timestamp"],
                        "hash": v["hash"],
                        "size": v["size"]
                    }
                    for v in file_info["versions"]
                ]

            transformed_files[file_id] = file_entry

        response["files"] = transformed_files

    if file_results and file_results["status"] == "success" and file_results["files_processed"] > 0:
        response["changed_files"] = file_results["changed_files"]

    return response

def generate_file_download_urls(
        current_user: str,
        session_id: str,
        files: Dict[str, Dict],
        expiration: int = 3600
) -> Dict[str, Dict]:
    """Generate presigned URLs for downloading files from S3."""
    try:
        s3_client = boto3.client('s3')
        bucket = os.getenv('AGENT_STATE_BUCKET')

        if not bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        # Get the index file for mappings and version history
        index_key = f"{current_user}/{session_id}/index.json"
        try:
            response = s3_client.get_object(
                Bucket=bucket,
                Key=index_key
            )
            index_content = json.loads(response['Body'].read().decode('utf-8'))
            filename_mappings = index_content.get('mappings', {})
            version_history = index_content.get('version_history', {})
        except ClientError as e:
            print(f"Error reading index file: {e}")
            return {}

        download_info = {}

        # Generate signed URLs for each file
        for file_id, file_details in files.items():
            original_name = file_details['original_name']
            s3_filename = file_details.get('s3_filename')

            if not s3_filename:
                print(f"Warning: No S3 filename found for {original_name}")
                continue

            s3_key = f"{current_user}/{session_id}/{s3_filename}"

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

                # Add version history if available
                if "versions" in file_details:
                    download_info[file_id]["versions"] = file_details["versions"]

            except ClientError as e:
                print(f"Error generating presigned URL for {original_name}: {e}")
                continue

        return download_info

    except Exception as e:
        print(f"Error generating download URLs: {e}")
        return {}


@vop(
    path="/vu-agent/get-file-download-urls",
    tags=["default"],
    name="getAgentFileDownloadUrls",
    description="Get file download URLs for a session.",
    params={
        "sessionId": "The session ID.",
        "files": "The files to get download URLs for.",
        "version_timestamp": "Optional timestamp to get a specific version",
    },
    schema={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string"},
            "files": {
                "type": "array",
                "description": "The file IDs to get download URLs for.",
                "items": {"type": "string"}
            },
            "version_timestamp": {
                "type": "string",
                "description": "Optional timestamp to get a specific version",
            }
        },
        "required": ["sessionId", "files"],
    }
)
def get_file_download_urls(current_user, access_token, session_id, files, version_timestamp=None):
    urls_by_file = {}
    for file_id in files:
        url = get_presigned_url_by_id(current_user, session_id, file_id)
        urls_by_file[file_id] = url

    return urls_by_file