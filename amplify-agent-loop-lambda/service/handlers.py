import json
import os
import traceback
import copy

import boto3

# Need to stay for the action registry to discover tools
import agent.tools.file_handling
import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec
import agent.tools.http_requests
import agent.tools.prompt_tools
import agent.tools.shell
import agent.tools.structured_editing
import agent.tools.markdown_converter
import agent.tools.database_tool

from agent.agents import actions_agent, workflow_agent
from agent.capabilities.workflow_model import Workflow
from agent.components.agent_registry import AgentRegistry
from agent.components.common_goals import Goal
from agent.components.python_action_registry import PythonActionRegistry
from agent.components.python_environment import PythonEnvironment
from agent.core import Action, UnknownActionError
from agent.prompt import create_llm
from agent.tools.ops import ops_to_tools, get_default_ops_as_tools
from pycommon.api.ops import api_tool
from datetime import datetime
from typing import List, Dict, Any
from service.data_sources import resolve_datasources
from botocore.exceptions import ClientError

from service.session_files import create_file_tracker, get_presigned_url_by_id
from workflow.workflow_template_registry import get_workflow_template
from pycommon.api.models import get_default_models
from aws_xray_sdk.core import patch_all, xray_recorder

patch_all()  # AWS x-ray patch all supported libraries (boto3, requests, etc.)


def save_conversation_state(
    current_user: str, session_id: str, conversation_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
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
        s3 = boto3.client("s3")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.getenv("AGENT_STATE_DYNAMODB_TABLE"))

        # Get bucket name from environment
        bucket = os.getenv("AGENT_STATE_BUCKET")
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
                ContentType="application/json",
            )
        except ClientError as e:
            print(f"Error storing conversation in S3: {e}")
            return {
                "success": False,
                "error": "Failed to store conversation in S3",
                "details": str(e),
            }

        # Update DynamoDB with S3 location
        try:
            table.update_item(
                Key={"user": current_user, "sessionId": session_id},
                UpdateExpression="SET memory = :memory, lastUpdated = :timestamp",
                ExpressionAttributeValues={
                    ":memory": {
                        "bucket": bucket,
                        "key": s3_key,
                        "lastModified": datetime.utcnow().isoformat(),
                    },
                    ":timestamp": datetime.utcnow().isoformat(),
                },
            )
        except ClientError as e:
            print(f"Error updating DynamoDB record: {e}")
            return {
                "success": False,
                "error": "Failed to update DynamoDB record",
                "details": str(e),
            }

        return {"success": True, "s3_location": {"bucket": bucket, "key": s3_key}}

    except Exception as e:
        print(f"Unexpected error in save_conversation_state: {e}")
        return {
            "success": False,
            "error": "Unexpected error occurred",
            "details": str(e),
        }


def event_printer(
    event_id: str, event: Dict[str, Any], current_user: str, session_id: str
):
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
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.getenv("AGENT_STATE_DYNAMODB_TABLE"))

        # Prepare item for DynamoDB
        item = {
            "user": current_user,
            "sessionId": session_id,
            "state": event["response"],
            "timestamp": datetime.utcnow().isoformat(),
            "eventId": event_id,
        }

        # Add context and correlation IDs if present
        if context_id_prefix != "na":
            item["contextId"] = context_id_prefix
        if correlation_id:
            item["correlationId"] = correlation_id

        try:
            # Write to DynamoDB
            table.put_item(Item=item)
            print(
                f"Stored agent response in DynamoDB for user {current_user}, session {session_id}"
            )
        except Exception as e:
            print(f"Error storing agent response in DynamoDB: {e}")


@api_tool(
    path="/vu-agent/handle-event",
    tags=["default"],
    name="agentHandleEvent",
    description="Trigger an agent to handle an event.",
    parameters={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string", "description": "The session ID."},
            "requestId": {"type": "string", "description": "The request ID."},
            "prompt": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["role", "content"],
                    "additionalProperties": True,
                },
                "description": "The prompt for the agent.",
            },
            "metadata": {"type": "object", "description": "Additional properties."},
        },
        "required": ["prompt", "sessionId"],
    },
    output={
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "The session ID"},
            "handled": {
                "type": "boolean",
                "description": "Whether the event was successfully handled",
            },
            "result": {
                "type": "array",
                "description": "Array of conversation results (role/content pairs)",
            },
            "files": {
                "type": "object",
                "description": "Session files with version information",
            },
            "changed_files": {
                "type": "array",
                "description": "List of files that were changed during processing",
            },
            "deleted_files": {
                "type": "array",
                "description": "List of files that were deleted during processing",
            },
            "error": {
                "type": "string",
                "description": "Error message if handling failed",
            },
        },
        "required": ["handled"],
    },
)
def handle_event(
    current_user,
    access_token,
    session_id,
    prompt,
    request_id=None,
    metadata=None,
    account_id="general_account",
    api_key_id=None,
    rate_limit=None,
):

    print(f"Handling Agent event for session {session_id} with prompt: {prompt}")

    try:
        work_directory = get_working_directory(session_id)

        with xray_recorder.in_subsegment("file_tracker_init"):
            tracker = create_file_tracker(current_user, session_id, work_directory)

        # Extract data sources from messages
        data_sources = extract_data_sources_from_messages(prompt)
        print(f"Data sources referenced in messages: {data_sources}")

        # Process data sources
        with xray_recorder.in_subsegment("datasource_init"):
            if data_sources:
                print(f"Processing {len(data_sources)} data sources")

                # Map to track which message each data source belongs to
                data_source_to_message_idx = {}
                for i, message in enumerate(prompt):
                    message_data_sources = message.get("data", {}).get(
                        "dataSources", []
                    )
                    for source in message_data_sources:
                        source_id = source.get("id")
                        if source_id:
                            data_source_to_message_idx[source_id] = i

                # Track successfully processed data sources with their local paths
                processed_data_sources = {}

                try:
                    datasource_request = {
                        "dataSources": data_sources,
                        "options": {"useSignedUrls": True},
                        "chat": {"messages": prompt},
                    }

                    resolved_result = resolve_datasources(
                        datasource_request, access_token
                    )
                    print(f"Resolved data sources: {resolved_result}")

                    if (
                        "dataSources" in resolved_result
                        and resolved_result["dataSources"]
                    ):
                        resolved_sources = resolved_result["dataSources"]

                        for source_details in resolved_sources:
                            print(f"Processing resolved data source: {source_details}")

                            # Add the data source name if missing
                            if "name" not in source_details:
                                file_name = source_details["id"].split("/")[-1]
                                source_details["name"] = file_name

                            # Check if this is an image that needs base64 decoding before processing
                            if source_details.get("type", "").startswith("image/"):
                                print(
                                    f"Processing potential base64 image: {source_details.get('id')}"
                                )

                            # Process the data source through the tracker
                            result = tracker.add_data_source(source_details)
                            print(f"Added data source {source_details}: {result}")

                            # If successful, store the information for enhancing the prompt
                            if result["status"] == "success":
                                source_id = source_details.get("id")
                                if source_id:
                                    processed_data_sources[source_id] = {
                                        "name": source_details.get("name", "file"),
                                        "type": source_details.get("type", "unknown"),
                                        "local_path": os.path.join(
                                            work_directory, result["local_path"]
                                        ),
                                    }
                    else:
                        # Fallback to the original data sources if resolution fails
                        for source_details in data_sources:
                            print(
                                f"Processing data source (fallback): {source_details}"
                            )
                            if source_details:
                                result = tracker.add_data_source(source_details)
                                print(f"Added data source {source_details}: {result}")

                                # If successful, store the information for enhancing the prompt
                                if result["status"] == "success":
                                    source_id = source_details.get("id")
                                    if source_id:
                                        processed_data_sources[source_id] = {
                                            "name": source_details.get("name", "file"),
                                            "type": source_details.get(
                                                "type", "unknown"
                                            ),
                                            "local_path": os.path.join(
                                                work_directory, result["local_path"]
                                            ),
                                        }
                            else:
                                print(
                                    f"Could not find full details for data source: {source_details}"
                                )

                except Exception as e:
                    print(f"Error resolving data sources: {e}")
                    traceback.print_exc()

                    # Fallback to original approach if resolution fails
                    for source_details in data_sources:
                        print(
                            f"Processing data source (error fallback): {source_details}"
                        )
                        if source_details:
                            result = tracker.add_data_source(source_details)
                            print(f"Added data source {source_details}: {result}")

                            # If successful, store the information for enhancing the prompt
                            if result["status"] == "success":
                                source_id = source_details.get("id")
                                if source_id:
                                    processed_data_sources[source_id] = {
                                        "name": source_details.get("name", "file"),
                                        "type": source_details.get("type", "unknown"),
                                        "local_path": os.path.join(
                                            work_directory, result["local_path"]
                                        ),
                                    }
                        else:
                            print(
                                f"Could not find full details for data source: {source_details}"
                            )

                # Enhance the prompt with data source information
                if processed_data_sources:
                    # Create a deep copy of the prompt to avoid modifying the original
                    enhanced_prompt = copy.deepcopy(prompt)

                    # Add conversational notes about data sources to the relevant messages
                    for source_id, source_info in processed_data_sources.items():
                        if source_id in data_source_to_message_idx:
                            message_idx = data_source_to_message_idx[source_id]

                            if (
                                message_idx < len(enhanced_prompt)
                                and "content" in enhanced_prompt[message_idx]
                            ):
                                note = f"\n\nI've attached {source_info['name']} to this message. It's a {source_info['type']} file stored at {source_info['local_path']}."

                                if isinstance(
                                    enhanced_prompt[message_idx]["content"], str
                                ):
                                    enhanced_prompt[message_idx]["content"] += note

                    # Use the enhanced prompt for the agent
                    prompt = enhanced_prompt
                    print("Enhanced prompt with data source information")

        metadata = metadata or {}
        agent_id = "default"

        additional_goals = [
            Goal(
                name="Work Directory",
                description=f"Any files you would like to save/write MUST be saved in {work_directory}. It is the only writable directory.",
            )
        ]

        workflow = None

        # Determine which Python built-in operations to include
        all_built_in_operations = []
        if metadata:
            built_in_operations = metadata.get("builtInOperations", [])
            assistant_data = metadata.get("assistant", {}).get("data", {})
            assistant_built_in_operations = assistant_data.get("builtInOperations", [])
            all_built_in_operations = (
                built_in_operations + assistant_built_in_operations
            )

            if "workflow" in metadata or "workflowTemplateId" in assistant_data:
                templateId = metadata.get("workflow", {}).get(
                    "templateId"
                ) or assistant_data.get("workflowTemplateId")
                print(f"Workflow templateId: {templateId}")
                if templateId:
                    print(f"Loading workflow template: {templateId}")
                    workflow_definition = get_workflow_template(
                        current_user, templateId
                    )
                    if workflow_definition:
                        try:
                            steps = workflow_definition["template"]["steps"]
                            workflow = Workflow.from_steps(steps, "")

                        except Exception as e:
                            print(f"Error loading workflow: {e}")

        tags = []
        tool_names = []
        if "*" in all_built_in_operations:
            tags, tool_names = None, None
        else:
            for operation in all_built_in_operations:
                if isinstance(operation, str):
                    if operation.startswith("tag:"):
                        tags.append(operation.replace("tag:", ""))
                    else:
                        tool_names.append(operation)

        print(f"Builtin operations: {all_built_in_operations}")
        print(f"Tags: {tags}")
        print(f"Tool names: {tool_names}")

        environment = PythonEnvironment()
        action_registry = PythonActionRegistry(tags=tags, tool_names=tool_names)

        for operation in all_built_in_operations:
            if isinstance(operation, dict):
                print("Registering built-in bound operation: ", operation)
                action_registry.register_bound_tool_by_name(operation)

        action_registry.register_terminate_tool()  # We always include terminate

        model = metadata.get("model")

        operations = metadata.get("operations", [])
        if "assistant" in metadata:
            assistant = metadata["assistant"]
            # enforce assistant selected model
            if "data" in assistant and "model" in assistant["data"]:
                print(
                    f"Enforcing assistant selected model: {assistant['data']['model']}"
                )
                model = assistant["data"]["model"]

            print(f"Assistant metadata: {assistant}")
            if assistant["instructions"]:
                print(
                    f"Adding assistant instructions to goals: {assistant['instructions']}"
                )
                additional_goals.append(
                    Goal(name="Instructions:", description=assistant["instructions"])
                )
            ops = assistant.get("data", {}).get("operations", [])
            print(f"Assistant operations: {ops}")
            operations.extend(ops)

        if operations:
            # print(f"Operations: {operations}")
            op_tools = ops_to_tools(operations)

            for op_tool in op_tools:
                print(f"Registering op tool in action registry: {op_tool['tool_name']}: {op_tool['description']}")
                print(f"Parameters: {op_tool.get('parameters', {})}")
                action_registry.register(
                    Action(
                        name=op_tool["tool_name"],
                        function=op_tool["function"],
                        description=op_tool["description"],
                        parameters=op_tool.get("parameters", {}),
                        output=op_tool.get("output", {}),
                        terminal=op_tool.get("terminal", False),
                    )
                )

        default_models = get_default_models(access_token)
        print(f"Using model: {model}")
        if not model:
            print(f"Default models: {default_models}")
            if not default_models.get("agent_model"):
                raise Exception("No model selected and no default model found")
            model = default_models.get("agent_model")

        with xray_recorder.in_subsegment("create_llm"):
            llm = create_llm(
                access_token,
                model,
                current_user,
                {"account_id": account_id, "api_key_id": api_key_id, "rate_limit": rate_limit},
                {"agent_session_id": session_id},
                default_models.get("advanced_model"),
            )

        if len(action_registry.actions.items()) > 3:
            action_registry.filter_tools_by_relevance(llm, prompt, additional_goals)

        print("Registered actions in action registry:")
        for tool_name, action in action_registry.actions.items():
            print(f"Registered action: {tool_name}")

        if workflow:
            workflow_tools_registry = PythonActionRegistry(tags=["workflow"])

            for action in workflow_tools_registry.get_actions():
                print(f"Adding workflow action: {action.name}")
                action_registry.register(action)

            op_tools_map = {}
            # ops_to_tools(ops)
            print(f"Validating workflow action bindings...")
            for i, step in enumerate(workflow.steps):
                action = action_registry.get_action(step.tool)
                if not action:
                    if not op_tools_map:
                        # find it and add it to the action registry
                        default_op_tools = get_default_ops_as_tools(access_token)
                        for op_tool in default_op_tools:
                            op_tools_map[op_tool["tool_name"]] = op_tool

                    # find the tool in op_tools
                    if step.tool in op_tools_map:
                        print("Registering step tool: ", step.tool)
                        step_tool = op_tools_map[step.tool]
                        action_registry.register(
                            Action(
                                name=step_tool["tool_name"],
                                function=step_tool["function"],
                                description=step_tool["description"],
                                parameters=step_tool.get("parameters", {}),
                                output=step_tool.get("output", {}),
                                terminal=step_tool.get("terminal", False),
                            )
                        )
                    elif not action_registry.register_tool_by_name(step.tool):
                        print(
                            f"Invalid Workflow. Action not found: Step {i+1}, Action: {step.tool}"
                        )
                        raise UnknownActionError(
                            f"Invalid Workflow. Action not found: Step {i+1}, Action: {step.tool}"
                        )

        if additional_goals and not workflow:
            print(f"Building action agent with additional goals: {additional_goals}")
            agent = actions_agent.build_clean(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                additional_goals=additional_goals,
            )
        elif not workflow:
            print(f"Building action agent with no additional goals.")
            agent = actions_agent.build(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                additional_goals=additional_goals,
            )
        else:
            print(f"Building workflow agent with workflow: {workflow}")
            agent = workflow_agent.build_clean(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                workflow=workflow,
                additional_goals=additional_goals,
            )

        agent_registry = AgentRegistry()
        agent_registry.register(
            "Action Agent",
            "Can use tools to take actions on behalf of the user.",
            agent,
        )

        # Create a wrapper function to pass additional parameters to event_printer
        def event_printer_wrapper(event_id: str, event: Dict[str, Any]):
            return event_printer(event_id, event, current_user, session_id)

        action_context_props = {
            "current_user": current_user,
            "request_id": request_id,
            "access_token": access_token,
            "account_id": account_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "event_handler": event_printer_wrapper,
            "agent_registry": agent_registry,
            "llm": llm,
            "work_directory": work_directory,
        }
        
        # Add attached database connection ID from metadata or chat body if present
        attached_database_id = None
        if metadata and metadata.get("attached_database_connection_id"):
            attached_database_id = metadata["attached_database_connection_id"]
        elif metadata and metadata.get("attachedDatabases"):
            # Handle attached databases from chat body
            attached_dbs = metadata.get("attachedDatabases")
            if isinstance(attached_dbs, list) and len(attached_dbs) > 0:
                attached_database_id = attached_dbs[0]  # Use first attached database
            elif isinstance(attached_dbs, str):
                attached_database_id = attached_dbs
        
        if attached_database_id:
            action_context_props["attachedDatabases"] = [attached_database_id]
            action_context_props["attached_database_connection_id"] = attached_database_id
            print(f"Added attached database connection ID to action context: {attached_database_id}")
        if api_key_id:
            action_context_props["api_key_id"] = api_key_id

        # Get the content of the last message from the prompt that is from the user
        user_last_message = next(
            (entry["content"] for entry in reversed(prompt) if entry["role"] == "user"),
            None,
        )

        # Remove any messages from the prompt that aren't user or assistant
        prompt = [entry for entry in prompt if entry["role"] in ["user", "assistant"]]
        # Combine all of the content attributes of the prompt entries into one string separated by newlines and
        # using the template: {role}: {content}
        user_input = "\n".join(
            [f"{entry['role']}: {entry['content']}" for entry in prompt]
        )

        with xray_recorder.in_subsegment("agent_run"):
            result = agent.run(
                user_input=user_input, action_context_props=action_context_props
            )

        # Find the first user message in the result and replace it with the last user message from the prompt
        # We do this so that the memories start from the last user message rather than the entire conversation
        for entry in result.items:
            if entry["type"] == "user":
                entry["content"] = user_last_message
                break

        def load_memory_content(memory):
            content = memory["content"]
            try:
                return json.loads(content)
            except:
                return content

        # Convert memory to a list of dicts
        processed_result = [
            {"role": item["type"], "content": load_memory_content(item)}
            for item in result.items
        ]

        total_token_cost = llm.get_total_cost()

        print(f"Total token cost: {total_token_cost}")

        processed_result.append(
            {"role": "environment", "content": {"total_token_cost": total_token_cost}}
        )

        # Save conversation state to S3 and update DynamoDB
        with xray_recorder.in_subsegment("save_conversation_state"):
            save_result = save_conversation_state(
                current_user, session_id, processed_result
            )

        if not save_result["success"]:
            print(f"Warning: Failed to save conversation state: {save_result['error']}")

        print(f"Conversation state saved to S3: {save_result['s3_location']}")
        print(f"Checking for changed files...")
        with xray_recorder.in_subsegment("upload_changed_files"):
            file_results = tracker.upload_changed_files()

        session_files = tracker.get_tracked_files()

        return build_response(
            session_id=session_id,
            current_user=current_user,
            processed_result=processed_result,
            file_results=file_results,
            session_files=session_files,
        )
    except UnknownActionError as ae:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {ae}")
        return {
            "handled": False,
            "error": f"An unknown action is referenced in the workflow. Check that the agent has that action defined."
            f" {str(ae)}",
        }

    except Exception as e:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {e}")
        return {"handled": False, "error": "Error handling event"}


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
    session_files: Dict = None,
) -> Dict:
    """
    Build a standardized response including files and their version history.
    """
    print(f"Building response for session {session_id}")
    response = {
        "session": session_id,
        "handled": True,
        "result": [item for item in processed_result if item["role"] != "prompt"],
    }

    if session_files:
        # Transform version info in session_files to use version_file_id
        transformed_files = {}
        for file_id, file_info in session_files.items():
            file_entry = {
                "original_name": file_info["original_name"],
                "size": file_info["size"],
                "last_modified": file_info["last_modified"],
            }

            if "versions" in file_info:
                file_entry["versions"] = [
                    {
                        "version_file_id": v.get("s3_name", "").rsplit(".", 1)[0],
                        "timestamp": v["timestamp"],
                        "hash": v["hash"],
                        "size": v["size"],
                    }
                    for v in file_info["versions"]
                ]

            transformed_files[file_id] = file_entry

        response["files"] = transformed_files

    if file_results and file_results["status"] == "success":
        if file_results.get("files_processed", 0) > 0:
            response["changed_files"] = file_results["changed_files"]
        if file_results.get("files_deleted", 0) > 0:
            response["deleted_files"] = file_results["deleted_files"]

    return response


def generate_file_download_urls(
    current_user: str, session_id: str, files: Dict[str, Dict], expiration: int = 3600
) -> Dict[str, Dict]:
    """Generate presigned URLs for downloading files from S3."""
    try:
        s3_client = boto3.client("s3")
        bucket = os.getenv("AGENT_STATE_BUCKET")

        if not bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        # Get the index file for mappings and version history
        index_key = f"{current_user}/{session_id}/index.json"
        try:
            response = s3_client.get_object(Bucket=bucket, Key=index_key)
            index_content = json.loads(response["Body"].read().decode("utf-8"))
            filename_mappings = index_content.get("mappings", {})
            version_history = index_content.get("version_history", {})
        except ClientError as e:
            print(f"Error reading index file: {e}")
            return {}

        download_info = {}

        # Generate signed URLs for each file
        for file_id, file_details in files.items():
            original_name = file_details["original_name"]
            s3_filename = file_details.get("s3_filename")

            if not s3_filename:
                print(f"Warning: No S3 filename found for {original_name}")
                continue

            s3_key = f"{current_user}/{session_id}/{s3_filename}"

            try:
                url = s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": s3_key},
                    ExpiresIn=expiration,
                )

                download_info[file_id] = {
                    "original_name": original_name,
                    "size": file_details["size"],
                    "last_modified": file_details["last_modified"],
                    "download_url": url,
                    "expires_in": expiration,
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


@api_tool(
    path="/vu-agent/get-file-download-urls",
    tags=["default"],
    name="getAgentFileDownloadUrls",
    description="Get file download URLs for a session.",
    parameters={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string", "description": "The session ID."},
            "files": {
                "type": "array",
                "description": "The file IDs to get download URLs for.",
                "items": {"type": "string"},
            },
            "version_timestamp": {
                "type": "string",
                "description": "Optional timestamp to get a specific version",
            },
        },
        "required": ["sessionId", "files"],
    },
    output={
        "type": "object",
        "description": "Object mapping file IDs to their download URLs",
        "additionalProperties": {"type": "string"},
    },
)
def get_file_download_urls(
    current_user, access_token, session_id, files, version_timestamp=None
):
    urls_by_file = {}
    for file_id in files:
        url = get_presigned_url_by_id(current_user, session_id, file_id)
        urls_by_file[file_id] = url

    return urls_by_file


def extract_data_sources_from_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Extracts all data sources from a list of messages.

    Args:
        messages: A list of message dictionaries that may contain data sources

    Returns:
        A list of data source objects
    """
    data_sources = []
    seen_source_ids = set()

    for message in messages:
        # Check if message has data and dataSources fields
        message_data_sources = message.get("data", {}).get("dataSources", [])

        # Add all data sources to our list, avoiding duplicates by ID
        if message_data_sources:
            print(f"Found data sources in message: {message_data_sources}")
            for source in message_data_sources:
                source_id = source.get("id")
                print(f"Processing data source: {source}")
                if source_id and source_id not in seen_source_ids:
                    print(f"Adding new data source: {source}")
                    seen_source_ids.add(source_id)
                    data_sources.append(source)

    return data_sources


@api_tool(
    path="/vu-agent/get-latest-agent-state",
    tags=[],
    name="getLatestAgentState",
    description="Polls latest entry from agent state logs",
    parameters={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string", "description": "The session ID."}
        },
        "required": ["sessionId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "inProgress": {
                "type": "boolean",
                "description": "Whether the agent is still processing (only present when success is true)",
            },
            "result": {
                "type": "array",
                "description": "Complete conversation results when processing is finished",
            },
            "session": {"type": "string", "description": "The session ID"},
            "files": {
                "type": "object",
                "description": "Session files with version information (when processing is complete)",
            },
            "state": {
                "type": "string",
                "description": "Current processing state (when still in progress)",
            },
            "metadata": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "event_id": {"type": "string"},
                    "context_id": {"type": "string"},
                    "correlation_id": {"type": "string"},
                },
                "description": "Processing metadata (when still in progress)",
            },
            "error": {
                "type": "string",
                "description": "Error message if operation failed",
            },
            "details": {
                "type": "string",
                "description": "Additional error details if operation failed",
            },
        },
        "required": ["success"],
    },
)
def get_latest_agent_state(current_user, session_id):
    try:
        # Initialize AWS clients
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.getenv("AGENT_STATE_DYNAMODB_TABLE"))

        # Query the table for the latest state for this user and session
        response = table.query(
            KeyConditionExpression="#user = :user AND #sessionId = :sessionId",
            ExpressionAttributeNames={"#user": "user", "#sessionId": "sessionId"},
            ExpressionAttributeValues={":user": current_user, ":sessionId": session_id},
            ScanIndexForward=False,  # Sort in descending order to get latest first
            Limit=1,
        )

        # Get the first item if it exists
        items = response.get("Items", [])
        if not items:
            return {"success": False, "error": "No agent state found for this session"}

        latest_item = items[0]

        # Check if this result has already been consumed
        if "consumedAt" in latest_item and latest_item["consumedAt"]:
            return {"success": False, "error": "No fresh results available"}

        # Check if the memory column is populated (indicating result is complete)
        if "memory" in latest_item and latest_item["memory"]:
            print(f"Agent result is complete, fetching conversation from S3")

            # Mark this result as consumed
            try:
                table.update_item(
                    Key={"user": current_user, "sessionId": session_id},
                    UpdateExpression="SET consumedAt = :timestamp",
                    ConditionExpression="attribute_exists(#ts)",
                    ExpressionAttributeNames={"#ts": "timestamp"},
                    ExpressionAttributeValues={":timestamp": datetime.utcnow().isoformat()}
                )
            except Exception as e:
                print(f"Warning: Failed to mark result as consumed: {e}")

            # Extract S3 location from memory
            memory = latest_item["memory"]
            bucket = memory.get("bucket")
            key = memory.get("key")

            if not bucket or not key:
                return {"success": False, "error": "Invalid S3 location in memory"}

            try:
                # Fetch conversation results from S3
                s3 = boto3.client("s3")
                s3_response = s3.get_object(Bucket=bucket, Key=key)
                conversation_data = json.loads(
                    s3_response["Body"].read().decode("utf-8")
                )

                # Fetch session files from index.json
                session_files = {}
                try:
                    index_key = f"{current_user}/{session_id}/index.json"
                    index_response = s3.get_object(Bucket=bucket, Key=index_key)
                    index_data = json.loads(
                        index_response["Body"].read().decode("utf-8")
                    )

                    filename_mappings = index_data.get("mappings", {})
                    version_history = index_data.get("version_history", {})

                    # Recreate the session files structure similar to get_tracked_files()
                    for filepath, s3_filename in filename_mappings.items():
                        # Remove extension to get base UUID (file_id)
                        file_id = s3_filename.rsplit(".", 1)[0]

                        # Get file stats from S3 if available
                        try:
                            file_s3_key = f"{current_user}/{session_id}/{s3_filename}"
                            file_response = s3.head_object(
                                Bucket=bucket, Key=file_s3_key
                            )
                            file_size = file_response["ContentLength"]
                            last_modified = file_response["LastModified"].isoformat()
                        except ClientError:
                            # Fallback values if we can't get file stats
                            file_size = 0
                            last_modified = datetime.utcnow().isoformat()

                        session_files[file_id] = {
                            "original_name": filepath,
                            "size": file_size,
                            "last_modified": last_modified,
                        }

                        # Add version history if available
                        if filepath in version_history:
                            session_files[file_id]["versions"] = [
                                {
                                    "version_file_id": v.get("s3_name", "").rsplit(
                                        ".", 1
                                    )[0],
                                    "timestamp": v["timestamp"],
                                    "hash": v["hash"],
                                    "size": v["size"],
                                }
                                for v in version_history[filepath]
                            ]

                except ClientError as e:
                    print(f"Could not fetch session files index: {e}")
                    # Continue without files if index is not available

                response_data = {
                    "success": True,
                    "inProgress": False,
                    "result": conversation_data,
                    "session": session_id,
                }

                # Add files if we have any
                if session_files:
                    response_data["files"] = session_files

                return response_data

            except ClientError as e:
                print(f"Error fetching conversation from S3: {e}")
                return {
                    "success": False,
                    "error": "Failed to fetch conversation results from S3",
                    "details": str(e),
                }
        else:
            # Result is not complete yet, return the current state
            return {
                "success": True,
                "inProgress": True,
                "state": latest_item.get("state"),
                "session": session_id,
                "metadata": {
                    "timestamp": latest_item.get("timestamp"),
                    "event_id": latest_item.get("eventId"),
                    "context_id": latest_item.get("contextId"),
                    "correlation_id": latest_item.get("correlationId"),
                },
            }

    except ClientError as e:
        print(f"Error retrieving DynamoDB record: {e}")
        return {
            "success": False,
            "error": "Failed to retrieve DynamoDB record",
            "details": str(e),
        }
    except Exception as e:
        print(f"Unexpected error in get_latest_agent_state: {e}")
        return {
            "success": False,
            "error": "Unexpected error occurred",
            "details": str(e),
        }
