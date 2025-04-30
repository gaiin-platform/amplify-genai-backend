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

from agent.agents import actions_agent, workflow_agent
from agent.capabilities.workflow_model import Workflow
from agent.components.agent_registry import AgentRegistry
from agent.components.common_goals import Goal
from agent.components.python_action_registry import PythonActionRegistry
from agent.components.python_environment import PythonEnvironment
from agent.core import Action, UnknownActionError, ActionRegistry
from agent.prompt import Prompt, create_llm
from agent.tools.ops import ops_to_tools, get_default_ops_as_tools
from common.ops import vop
from datetime import datetime
from typing import List, Dict, Any
from botocore.exceptions import ClientError

from service.session_files import create_file_tracker, get_presigned_url_by_id
from workflow.workflow_template_registry import list_workflow_templates, get_workflow_template, \
    register_workflow_template, delete_workflow_template, update_workflow_template
from service.models import get_default_models

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
            "requestId": {"type": "string"},
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
def handle_event(current_user, access_token, session_id, prompt, request_id=None, metadata=None, account_id="general_account"):

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

        workflow = None

        # Determine which Python built-in operations to include
        all_built_in_operations = []
        if metadata:

            built_in_operations = metadata.get('builtInOperations', [])
            assistant_data = metadata.get('assistant', {}).get('data',{})
            assistant_built_in_operations = assistant_data.get('builtInOperations', [])
            all_built_in_operations = built_in_operations + assistant_built_in_operations

            if 'workflow' in metadata or 'workflowTemplateId' in assistant_data:
                templateId = metadata.get("workflow",{}).get("templateId") or assistant_data.get("workflowTemplateId")
                print(f"Workflow templateId: {templateId}")
                if templateId:
                    print(f"Loading workflow template: {templateId}")
                    workflow_definition = get_workflow_template(current_user, templateId)
                    if workflow_definition:
                        try:
                            steps = workflow_definition['template']['steps']
                            workflow = Workflow.from_steps(steps, "")

                        except Exception as e:
                            print(f"Error loading workflow: {e}")

        tags = []
        tool_names = []
        if '*' in all_built_in_operations:
            tags, tool_names = None, None
        else:
            for operation in all_built_in_operations:
                if operation.startswith('tag:'):
                    tags.append(operation.replace('tag:', ''))
                else:
                    tool_names.append(operation)

        print(f"Builtin operations: {all_built_in_operations}")
        print(f"Tags: {tags}")
        print(f"Tool names: {tool_names}")

        environment = PythonEnvironment()
        action_registry = PythonActionRegistry(tags=tags, tool_names=tool_names)

        action_registry.register_terminate_tool() # We always include terminate

        model = metadata.get('model')

        if 'assistant' in metadata:
            assistant = metadata['assistant']
            print(f"Assistant metadata: {metadata['assistant']}")
            if assistant['instructions']:
                print(f"Adding assistant instructions to goals: {assistant['instructions']}")
                additional_goals.append(Goal(
                    name="Instructions:",
                    description=assistant['instructions']
                ))
            ops = assistant.get("data",{}).get("operations", [])
            print(f"Assistant operations: {ops}")
            op_tools = ops_to_tools(ops)

            for op_tool in op_tools:
                print(f"Registering op tool in action registry: {op_tool['tool_name']}: {op_tool['description']}")
                print(f"Parameters: {op_tool.get('parameters', {})}")
                action_registry.register(
                    Action(
                        name=op_tool['tool_name'],
                        function=op_tool["function"],
                        description=op_tool["description"],
                        parameters=op_tool.get("parameters", {}),
                        output=op_tool.get("output", {}),
                        terminal=op_tool.get("terminal", False)
                    )
                )
            # enforce assistant selected model
            if ("data" in assistant and "model" in assistant["data"]):
                model = assistant["data"]["model"]


        default_models = get_default_models(access_token)
        print(f"Using model: {model}")
        if not model:
            print(f"Default models: {default_models}")
            if not default_models.get("agent_model"):
                raise Exception("No model selected and no default model found")
            model = default_models.get("agent_model")

        llm = create_llm(access_token, model, current_user, account_id, {"agent_session_id": session_id}, default_models.get("advanced_model"))

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
                            op_tools_map[op_tool['tool_name']] = op_tool
                    
                    # find the tool in op_tools
                    if (step.tool in op_tools_map):
                        print("Registering step tool: ", step.tool)
                        step_tool = op_tools_map[step.tool]
                        action_registry.register(
                            Action(
                                name=step_tool['tool_name'],
                                function=step_tool["function"],
                                description=step_tool["description"],
                                parameters=step_tool.get("parameters", {}),
                                output=step_tool.get("output", {}),
                                terminal=step_tool.get("terminal", False)
                            )
                        )
                    elif (not action_registry.register_tool_by_name(step.tool)):
                        print(f"Invalid Workflow. Action not found: Step {i+1}, Action: {step.tool}")
                        raise UnknownActionError(f"Invalid Workflow. Action not found: Step {i+1}, Action: {step.tool}")


        if additional_goals and not workflow:
            print(f"Building action agent with additional goals: {additional_goals}")
            agent = actions_agent.build_clean(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                additional_goals=additional_goals)
        elif not workflow:
            print(f"Building action agent with no additional goals.")
            agent = actions_agent.build(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                additional_goals=additional_goals)
        else:
            print(f"Building workflow agent with workflow: {workflow}")
            agent = workflow_agent.build_clean(
                environment=environment,
                action_registry=action_registry,
                generate_response=llm,
                workflow=workflow,
                additional_goals=additional_goals)


        agent_registry = AgentRegistry()
        agent_registry.register("Action Agent", "Can use tools to take actions on behalf of the user.", agent)

        # Create a wrapper function to pass additional parameters to event_printer
        def event_printer_wrapper(event_id: str, event: Dict[str, Any]):
            return event_printer(event_id, event, current_user, session_id)

        action_context_props={
            'current_user': current_user,
            'request_id': request_id,
            'access_token': access_token,
            'account_id': account_id,   
            'session_id': session_id,
            'agent_id': agent_id,
            "event_handler": event_printer_wrapper,
            "agent_registry": agent_registry,
            "llm": llm,
            "work_directory": work_directory,
        }

        # Get the content of the last message from the prompt that is from the user
        user_last_message = next((entry['content'] for entry in reversed(prompt) if entry['role'] == 'user'), None)

        # Remove any messages from the prompt that aren't user or assistant
        prompt = [entry for entry in prompt if entry['role'] in ['user', 'assistant']]
        # Combine all of the content attributes of the prompt entries into one string separated by newlines and
        # using the template: {role}: {content}
        user_input = "\n".join([f"{entry['role']}: {entry['content']}" for entry in prompt])

        result = agent.run(user_input=user_input, action_context_props=action_context_props)

        # Find the first user message in the result and replace it with the last user message from the prompt
        # We do this so that the memories start from the last user message rather than the entire conversation
        for entry in result.items:
            if entry['type'] == 'user':
                entry['content'] = user_last_message
                break

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
        
        total_token_cost = llm.get_total_cost()

        print(f"Total token cost: {total_token_cost}")

        processed_result.append({
            "role": "environment",
            "content": { "total_token_cost": total_token_cost }
        })

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
    except UnknownActionError as ae:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {ae}")
        return {
            "handled": False,
            "error": f"An unknown action is referenced in the workflow. Check that the agent has that action defined."
                     f" {str(ae)}"
        }

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
        "result": [item for item in processed_result if item['role'] != 'prompt']
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

@vop(
    path="/vu-agent/register-workflow-template",
    tags=["workflows"],
    name="registerWorkflowTemplate",
    description="Register a new workflow template.",
    params={
        "template": "The workflow template definition with steps",
        "name": "Name of the template",
        "description": "Description of the template",
        "inputSchema": "Schema defining the template inputs",
        "outputSchema": "Schema defining the template outputs"
    },
    schema={
        "type": "object",
        "properties": {
            "template": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "tool": {"type": "string"},
                                "instructions": {"type": "string"},
                                "values": {"type": "object", "additionalProperties": {"type": "string"}},
                                "args": {"type": "object", "additionalProperties": {"type": "string"}},
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {"type": "array", "items": {"type": "string"}},
                                "useAdvancedReasoning": {"type": "boolean"}
                            },
                            "required": ["tool", "instructions"]
                        }
                    }
                },
                "required": ["steps"]
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "isBaseTemplate": {"type": "boolean"},
            "isPublic": {"type": "boolean"}
        },
        "required": ["template", "name", "description", "inputSchema", "outputSchema"]
    }
)
def register_workflow_template_handler(current_user, access_token, template, name, description, input_schema, output_schema, is_base_template=False, is_public=False):
    try:
        template_id = register_workflow_template(  # Use template_id as per earlier changes
            current_user=current_user,
            template=template,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            is_base_template=is_base_template,
            is_public=is_public
        )
        print(f"Registered workflow template: {template_id}")
        return {"templateId": template_id}  # Use camel case in response
    except Exception as e:
        print(f"Error registering workflow template: {e}")
        raise RuntimeError(f"Failed to register workflow template: {str(e)}")

@vop(
    path="/vu-agent/delete-workflow-template",
    tags=["workflows"],
    name="deleteWorkflowTemplate",
    description="Delete a workflow template by ID.",
    params={
        "templateId": "ID of the template to delete"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"}
        },
        "required": ["templateId"]
    }
)
def delete_workflow_template_handler(current_user, access_token, template_id):
    try:
        result = delete_workflow_template(current_user, template_id)
        print(f"Delete workflow template result: {result}")
        return result
    except Exception as e:
        print(f"Error deleting workflow template: {e}")
        raise RuntimeError(f"Failed to delete workflow template: {str(e)}")


@vop(
    path="/vu-agent/get-workflow-template",
    tags=["workflows"],
    name="getWorkflowTemplate",
    description="Get a workflow template by ID.",
    params={
        "templateId": "ID of the template to retrieve"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"}
        },
        "required": ["templateId"]
    }
)
def get_workflow_template_handler(current_user, access_token, template_id):
    try:
        template = get_workflow_template(current_user, template_id)
        if template is None:
            raise ValueError("Template not found")
        return template  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to get workflow template: {str(e)}")

@vop(
    path="/vu-agent/list-workflow-templates",
    tags=["workflows"],
    name="listWorkflowTemplates",
    description="List all workflow templates for the current user.",
    schema={
       "type": "object",
        "properties": {
            "filterBaseTemplates": {"type": "boolean"}
        },
        "required": []
    },
    params={"filterBaseTemplates": "Optional boolean to filter for base templates only"}
)
def list_workflow_templates_handler(current_user, access_token, filter_base_templates=False):
    try:
        templates = list_workflow_templates(current_user)
        if filter_base_templates:
            templates = [t for t in templates if t['isBaseTemplate']]
        return {"templates": templates}  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to list workflow templates: {str(e)}")

@vop(
    path="/vu-agent/update-workflow-template",
    tags=["workflows"],
    name="updateWorkflowTemplate",
    description="Update an existing workflow template.",
    params={
        "templateId": "ID of the template to update",
        "template": "The updated workflow template definition with steps",
        "name": "Updated name of the template",
        "description": "Updated description of the template",
        "inputSchema": "Updated schema defining the template inputs",
        "outputSchema": "Updated schema defining the template outputs",
        "isBaseTemplate": "Whether this is a base template",
        "isPublic": "Whether this template is publicly accessible"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"},
            "template": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "tool": {"type": "string"},
                                "instructions": {"type": "string"},
                                "values": {"type": "object", "additionalProperties": {"type": "string"}},
                                "args": {"type": "object", "additionalProperties": {"type": "string"}},
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {"type": "array", "items": {"type": "string"}},
                                "useAdvancedReasoning": {"type": "boolean"}
                            },
                            "required": ["tool", "instructions"]
                        }
                    }
                },
                "required": ["steps"]
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "isBaseTemplate": {"type": "boolean"},
            "isPublic": {"type": "boolean"}
        },
        "required": ["templateId", "template", "name", "description", "inputSchema", "outputSchema"]
    }
)
def update_workflow_template_handler(current_user, access_token, template_id, template, name, description, 
                                      input_schema, output_schema, is_base_template=False, is_public=False):
    try:
        result = update_workflow_template(
            current_user=current_user,
            template_id=template_id,
            template=template,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            is_base_template=is_base_template,
            is_public=is_public
        )
        print(f"Updated workflow template: {template_id}")
        return result
    except Exception as e:
        print(f"Error updating workflow template: {e}")
        raise RuntimeError(f"Failed to update workflow template: {str(e)}")