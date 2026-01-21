import requests
from pycommon.llm.chat import chat
import os
from pycommon.api.get_endpoint import get_endpoint, EndpointType
import json
import os
import boto3
from decimal import Decimal
from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
import asyncio
import logging
import time
from datetime import datetime
import re

# MCP Integration imports - Simple working version
from chat.mcp_chat_integration import get_mcp_chat_integration
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.CHAT.value])

# Configure logging for MCP integration
logger = logging.getLogger(__name__)

# Simple MCP integration
mcp_integration = None

# Global variable to store MCP metadata for the current request
current_mcp_metadata = None


async def initialize_mcp_integration(current_user=None):
    """Initialize simple MCP integration."""
    global mcp_integration

    try:
        logger.info("Initializing simple MCP integration")
        mcp_integration = get_mcp_chat_integration()

        tools_count = len(mcp_integration.get_tools_for_ai())
        logger.info(f"Simple MCP integration initialized with {tools_count} tools")

        return True

    except Exception as e:
        logger.error(f"Failed to initialize MCP integration: {e}")
        return False


async def get_mcp_tools_for_ai(limit=20):
    """Get MCP tools formatted for AI model function calls."""
    if not mcp_integration:
        return []

    try:
        tools = mcp_integration.get_tools_for_ai()
        if limit and len(tools) > limit:
            tools = tools[:limit]
        logger.info(f"Retrieved {len(tools)} MCP tools for AI model")
        return tools
    except Exception as e:
        logger.error(f"Error getting MCP tools for AI: {e}")
        return []


async def execute_mcp_function_call(function_name, arguments):
    """Execute an MCP function call using simple integration."""
    if not mcp_integration:
        raise RuntimeError("MCP integration not initialized")

    try:
        logger.info(f"Executing MCP tool: {function_name} with arguments: {json.dumps(arguments)}")
        start_time = time.time()
        start_timestamp = datetime.utcnow().isoformat() + 'Z'

        # Execute the function using simple integration
        result = await mcp_integration.execute_function_call(function_name, arguments)

        end_time = time.time()
        end_timestamp = datetime.utcnow().isoformat() + 'Z'
        execution_time = end_time - start_time

        logger.info(f"MCP tool {function_name} executed in {execution_time:.2f}s")

        return {
            **result,  # Include success, content, error fields from simple integration
            "execution_time": execution_time,
            "function_name": function_name,
            "start_time": start_timestamp,
            "end_time": end_timestamp
        }

    except Exception as e:
        logger.error(f"Error executing MCP function {function_name}: {e}")
        end_timestamp = datetime.utcnow().isoformat() + 'Z'
        return {
            "success": False,
            "error": str(e),
            "function_name": function_name,
            "start_time": start_timestamp if 'start_timestamp' in locals() else end_timestamp,
            "end_time": end_timestamp
        }


async def cleanup_mcp_integration():
    """Clean up MCP integration resources."""
    global mcp_integration

    try:
        if mcp_integration:
            # Simple integration doesn't need cleanup
            logger.info("MCP integration cleaned up successfully")
    except Exception as e:
        logger.error(f"Error during MCP cleanup: {e}")


def process_mcp_function_calls(messages, mcp_results):
    """Process MCP function call results and inject them into the conversation."""
    if not mcp_results:
        return messages

    # Make a copy of messages to avoid modifying the original
    enhanced_messages = messages.copy()

    # Always add function call results as system messages (even if no assistant message exists yet)
    for result in mcp_results:
        if result.get('success'):
            function_name = result.get('function_name', 'unknown')
            if 'content' in result:
                # File read result - show the actual content
                function_result_message = {
                    'role': 'system',
                    'content': f"MCP Tool Result from {function_name}: Successfully read file. Content:\n{result['content']}"
                }
            else:
                # Other results - show full result
                function_result_message = {
                    'role': 'system',
                    'content': f"MCP Tool Result from {function_name}: {json.dumps(result, indent=2)}"
                }
        else:
            error_msg = result.get('error', 'Unknown error')
            if isinstance(error_msg, dict):
                error_msg = json.dumps(error_msg)
            elif not isinstance(error_msg, str):
                error_msg = str(error_msg)

            function_result_message = {
                'role': 'system',
                'content': f"MCP Tool Error for {result.get('function_name', 'unknown')}: {error_msg}"
            }

        enhanced_messages.append(function_result_message)
        print(f"TRACE: Added system message for MCP result: {function_result_message['content'][:100]}...")

    # Find the last assistant message to add MCP metadata (for frontend banners)
    for i in range(len(enhanced_messages) - 1, -1, -1):
        if enhanced_messages[i].get('role') == 'assistant':
            # Prepare MCP metadata for the frontend banner
            mcp_tools = []
            mcp_status = []

            for result in mcp_results:
                function_name = result.get('function_name', 'unknown')
                server_name = function_name.split('.')[0] if '.' in function_name else 'unknown'
                tool_name = function_name.split('.')[1] if '.' in function_name else function_name

                # Add tool information for frontend display
                mcp_tools.append({
                    'name': tool_name,
                    'server': server_name,
                    'qualified_name': function_name,
                    'description': f"MCP tool: {tool_name}",
                    'category': 'file_operations' if 'file' in tool_name.lower() else 'utility'
                })

                # Add execution status for frontend display
                mcp_status.append({
                    'status': 'completed' if result.get('success') else 'failed',
                    'tool_name': tool_name,
                    'server': server_name,
                    'start_time': result.get('start_time'),
                    'end_time': result.get('end_time'),
                    'message': result.get('error') if not result.get('success') else 'Executed successfully'
                })

            # Add MCP metadata to assistant message data field for frontend banners
            if not enhanced_messages[i].get('data'):
                enhanced_messages[i]['data'] = {}

            enhanced_messages[i]['data']['mcpTools'] = mcp_tools
            enhanced_messages[i]['data']['mcpStatus'] = mcp_status

            logger.info(f"Added MCP metadata to assistant message: {len(mcp_tools)} tools, {len(mcp_status)} status entries")
            break

    return enhanced_messages


@api_tool(
    path="/chat",
    name="chatWithAmplify",
    method="POST",
    tags=["apiDocumentation"],
    description="""Interact with Amplify via real-time streaming chat capabilities, utilizing advanced AI models. 
    Example request: 
     {
    "data":{
        "temperature": 0.7,
        "max_tokens": 4000,
        "dataSources": ["yourEmail@vanderbilt.edu/2014-qwertyuio.json"],
        "messages": [
            {
            "role": "user",
            "content": "What is the capital of France?"
            }
        ],
        "options": {
            "ragOnly": false,
            "skipRag": true,
            "model": {"id": "gpt-4o"},
            "assistantId": "astp/abcdefghijk",
            "prompt": "What is the capital of France?"
        }
    }
}""",
    parameters={
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "String representing the model ID. User can request a list of the models by calling the /available_models endpoint",
            },
            "temperature": {
                "type": "number",
                "description": "Float value controlling the randomness of responses. Example: 0.7 for balanced outputs.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Integer representing the maximum number of tokens the model can generate in the response. Typically never over 2048. The user can confirm the max tokens for each model by calling the /available_models endpoint",
            },
            "dataSources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings representing input file ids, primarily used for retrieval-augmented generation (RAG). The user can make a call to the /files/query endpoint to get the id for their file. In the case of uploading a new data source through the /files/upload endpoint, the user can use the returned key as the id.",
            },
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "description": "Array of objects representing the conversation history. Each object includes 'role' (system/assistant/user) and 'content' (the message text). Example: [{'role': 'user', 'content': 'What is the capital of France?'}].",
            },
            "options": {
                "type": "object",
                "properties": {
                    "ragOnly": {
                        "type": "boolean",
                        "description": "Boolean indicating whether only retrieval-augmented responses should be used. Example: false.",
                    },
                    "skipRag": {
                        "type": "boolean",
                        "description": "Boolean indicating whether to skip retrieval-augmented generation. Example: true.",
                    },
                    "assistantId": {
                        "type": "string",
                        "description": "String prefixed with 'astp' to identify the assistant. Example: 'astp/abcdefghijk'.",
                    },
                    "model": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "description": "Object containing model-specific configurations, including 'id'. Example: {'id': 'gpt-4o'}. Must match the model id under the model attribute",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "String representing a system prompt for the model.",
                    },
                },
            },
        },
        "required": ["messages", "options"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the chat request was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "description": "Chat response data from the AI model",
            },
        },
        "required": ["success", "message"],
    },
)
def chat_endpoint_raw(event, context):
    """Raw chat endpoint that returns streaming format for frontend compatibility."""
    try:
        # Parse the request manually since we're not using @validated
        body = json.loads(event.get("body", "{}"))
        data = body.get("data", {})

        # Get user claims manually
        headers = event.get("headers", {})
        auth_header = headers.get("authorization", "") or headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "text/plain"},
                "body": "Unauthorized"
            }

        token = auth_header.split(" ")[1]
        from pycommon.authz import get_claims
        claims = get_claims(token)
        current_user = claims["username"]

        # Prepare data structure like the validated decorator does
        full_data = {
            "data": data,
            "access_token": token,
            "account": claims["account"],
            "rate_limit": claims["rate_limit"],
            "api_accessed": False,
            "allowed_access": claims["allowed_access"]
        }

        # Run the async chat logic - access_token is a separate parameter
        result = asyncio.run(async_chat_endpoint(event, context, current_user, "/chat", full_data, token))

        # Convert response to SSE format
        content = str(result)
        # Escape content for JSON
        escaped_content = content.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')

        # Inject MCP metadata if available
        sse_response = f'data: {{"d": "{escaped_content}", "s": "message"}}\n\n'

        global current_mcp_metadata
        if current_mcp_metadata:
            print(f"RAW ENDPOINT: Injecting MCP metadata: {len(current_mcp_metadata.get('mcpTools', []))} tools")
            mcp_data_line = f'data: {{"mcp": {json.dumps(current_mcp_metadata)}, "s": "mcp_metadata"}}\n\n'
            sse_response += mcp_data_line
            # Clear the metadata after use
            current_mcp_metadata = None

        sse_response += 'data: [DONE]\n\n'

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            },
            "body": sse_response
        }

    except Exception as e:
        import traceback
        print(f"Error in raw chat endpoint: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "text/plain"},
            "body": f"Error: {str(e)}"
        }

@validated("chat")
def chat_endpoint(event, context, current_user, name, data):
    import traceback
    print("TRACE: chat_endpoint function entered!")
    logger.info("TRACE: chat_endpoint function entered via logger!")
    try:
        import traceback
        logger.info("TRACE: chat_endpoint started")

        access_token = data["access_token"]
        access = data["allowed_access"]
        if APIAccessType.CHAT.value not in access and APIAccessType.FULL_ACCESS.value not in access:
            return {
                "success": False,
                "message": "API key does not have access to chat functionality",
            }

        logger.info("TRACE: About to run async_chat_endpoint")
        # Run the async chat logic
        try:
            result = asyncio.run(async_chat_endpoint(event, context, current_user, name, data, access_token))
            print("TRACE: asyncio.run completed successfully")
            logger.info("TRACE: async_chat_endpoint completed successfully")
            print(f"TRACE: Result type: {type(result)}")
            print(f"TRACE: Result success: {result.get('success') if isinstance(result, dict) else 'Not a dict'}")
            return result
        except Exception as e:
            logger.error(f"TRACE: asyncio.run failed: {e}")
            logger.error(f"TRACE: asyncio.run traceback: {traceback.format_exc()}")
            return {"success": False, "message": f"Async execution failed: {str(e)}"}
    except Exception as e:
        import traceback
        logger.error(f"TRACE: Exception in chat_endpoint: {e}")
        logger.error(f"TRACE: chat_endpoint traceback: {traceback.format_exc()}")
        raise


async def async_chat_endpoint(event, context, current_user, name, data, access_token):
    """Async chat endpoint with MCP integration."""
    global current_mcp_metadata
    print("TRACE: async_chat_endpoint function ENTERED!")

    # Clear any previous MCP metadata
    current_mcp_metadata = None

    try:
        import traceback
        print("TRACE: async_chat_endpoint started with logger")
        logger.info("TRACE: async_chat_endpoint started")
        print("TRACE: About to call get_endpoint")
        chat_endpoint_url = get_endpoint(EndpointType.CHAT_ENDPOINT)
        print("TRACE: get_endpoint completed")
        print(f"TRACE: chat_endpoint_url type: {type(chat_endpoint_url)}")
        print(f"TRACE: chat_endpoint_url value: {chat_endpoint_url}")

        # Ensure chat_endpoint_url is a string (AWS Secrets Manager might return dict)
        print("TRACE: About to do type conversion")
        if isinstance(chat_endpoint_url, dict):
            chat_endpoint_url = str(chat_endpoint_url)
            print("TRACE: Converted dict to string")
        elif not isinstance(chat_endpoint_url, str):
            chat_endpoint_url = str(chat_endpoint_url) if chat_endpoint_url else ""
            print("TRACE: Converted non-string to string")
        else:
            print("TRACE: chat_endpoint_url is already a string")

        print("TRACE: Type conversion completed")
        print(f"TRACE: Retrieved chat_endpoint_url: {type(chat_endpoint_url)} = {chat_endpoint_url}")

        print("TRACE: About to check if chat_endpoint_url exists")
        if not chat_endpoint_url:
            print("TRACE: chat_endpoint_url is empty, returning error")
            return {
                "success": False,
                "message": "We are unable to make the request. Error: No chat endpoint found.",
            }

        print("TRACE: chat_endpoint_url exists, continuing")
        print("TRACE: About to extract payload")
        payload = data["data"]
        print("TRACE: Payload extracted successfully")
        print("TRACE: About to get assistant_id")
        assistant_id = payload["options"].get("assistantId")
        print(f"TRACE: assistant_id = {assistant_id}")
        if assistant_id:
            print("TRACE: assistant_id exists, validating")
            verify_assistant_id = validate_assistant_id(assistant_id, access_token)
            print("TRACE: assistant_id validation completed")
            if not verify_assistant_id["success"]:
                assistant_id_str = assistant_id
                if isinstance(assistant_id_str, dict):
                    assistant_id_str = json.dumps(assistant_id_str)
                elif not isinstance(assistant_id_str, str):
                    assistant_id_str = str(assistant_id_str)

                print(f"Invalid assistant id: {assistant_id_str}")
                return {"success": False, "message": "Invalid assistant id"}
        else:
            print("TRACE: No assistant_id to validate")

        print("TRACE: About to process data sources")
        try:
            payload["dataSources"] = get_data_source_details(payload["dataSources"])
            print("TRACE: Data sources processed successfully")
        except Exception as e:
            import traceback
            logger.error(f"Error in get_data_source_details: {e}")
            logger.error(f"get_data_source_details traceback: {traceback.format_exc()}")
            raise
        print("TRACE: About to extract payload options")
        payload_options = payload["options"]
        payload["model"] = payload_options["model"]["id"]
        messages = payload["messages"]
        print("TRACE: Payload options extracted successfully")

        SYSTEM_ROLE = "system"
        if messages[0]["role"] != SYSTEM_ROLE:
            print("Adding system prompt message")
            user_prompt = payload_options.get("prompt", "No Prompt Provided")
            payload["messages"] = [
                {"role": SYSTEM_ROLE, "content": user_prompt}
            ] + messages

        # Initialize MCP integration
        print("TRACE: About to initialize MCP integration")
        mcp_initialized = False
        mcp_tools = []
        mcp_function_results = []

        try:
            print("TRACE: Starting MCP initialization")
            logger.info("Initializing MCP integration for chat request")
            print("TRACE: About to call initialize_mcp_integration")
            mcp_initialized = await initialize_mcp_integration(current_user)
            print(f"TRACE: MCP initialization completed: {mcp_initialized}")
            logger.info(f"MCP initialization result: {mcp_initialized}")

            if mcp_initialized:
                # Get MCP tools for the AI model
                mcp_tools = await get_mcp_tools_for_ai(limit=20)
                logger.info(f"Retrieved {len(mcp_tools)} MCP tools for AI model")
                logger.info(f"Model: {payload.get('model')}")
                logger.info(f"Tools: {json.dumps(mcp_tools, indent=2)}")

                # Add MCP tools to the payload if the model supports function calls
                supported_models = [
                    "gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
                    # Claude models - include all variants
                    "claude-3-sonnet", "claude-3-5-sonnet", "claude-3-7-sonnet", "claude-3-haiku", "claude-3-opus"
                ]
                model_name = payload.get("model", "")
                # Check if model name contains any supported model patterns
                model_supported = any(supported in model_name for supported in supported_models)

                logger.info(f"Model matching check: model='{model_name}', supported={model_supported}")
                logger.info(f"Supported patterns: {supported_models}")

                if mcp_tools and model_supported:
                    if "tools" not in payload:
                        payload["tools"] = []
                    payload["tools"].extend(mcp_tools)

                    # Enable function calling
                    payload["tool_choice"] = "auto"
                    logger.info(f"Added MCP tools to payload. Total tools: {len(payload['tools'])}")
                else:
                    model_info = payload.get('model')
                    if isinstance(model_info, dict):
                        model_info = json.dumps(model_info)
                    elif not isinstance(model_info, str):
                        model_info = str(model_info)

                    logger.warning(f"MCP tools not added. Tools count: {len(mcp_tools)}, Model: {model_info}")

        except Exception as e:
            print(f"TRACE: MCP initialization FAILED with exception: {e}")
            logger.error(f"MCP initialization failed, continuing without MCP: {e}")
            import traceback
            print(f"TRACE: MCP initialization traceback: {traceback.format_exc()}")
            logger.error(f"MCP initialization traceback: {traceback.format_exc()}")
            mcp_initialized = False
            print("TRACE: Set mcp_initialized = False due to exception")

        # Make the chat request
        print("TRACE: About to make chat request")
        try:
            print("TRACE: Starting chat function call")
            logger.info("About to call chat function")
            print(f"TRACE: Chat endpoint URL: {chat_endpoint_url}")
            print(f"TRACE: Payload keys: {list(payload.keys())}")
            print(f"TRACE: Payload model: {payload.get('model')}")
            print(f"TRACE: Payload tools count: {len(payload.get('tools', []))}")

            response, metadata = chat(chat_endpoint_url, access_token, payload)
            print("TRACE: Chat function returned successfully")
            logger.info("Chat function completed successfully")
            print(f"TRACE: Response type: {type(response)}")
            print(f"TRACE: Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            if isinstance(response, str):
                print(f"TRACE: Response content preview: {response[:500]}")

            print(f"TRACE: MCP initialized: {mcp_initialized}")
            print(f"TRACE: Response has choices: {'choices' in response if isinstance(response, dict) else False}")

            # Process MCP function calls if present in response
            if mcp_initialized and response:
                print("TRACE: Entering MCP tool call processing")
                tool_calls = []

                # Parse streaming response for function calls
                if isinstance(response, str):
                    print(f"TRACE: Parsing streaming response for tool calls")

                    # Parse streaming tool calls from response
                    import re
                    tool_call_data = {}

                    # Find all JSON objects in the response
                    json_pattern = r'\{"tool_calls":\s*\[.*?\]\}'
                    matches = re.findall(json_pattern, response)

                    print(f"TRACE: Found {len(matches)} tool call chunks")

                    # Process each JSON chunk to accumulate tool call data
                    for match in matches:
                        try:
                            chunk_data = json.loads(match)
                            if "tool_calls" in chunk_data and chunk_data["tool_calls"]:
                                tool_call = chunk_data["tool_calls"][0]

                                # Get or create tool call entry - use index for grouping since subsequent chunks may not have id
                                call_index = tool_call.get("index", 0)
                                call_id = tool_call.get("id", f"call_{call_index}")

                                if call_index not in tool_call_data:
                                    tool_call_data[call_index] = {
                                        "id": call_id,
                                        "type": tool_call.get("type", "function"),
                                        "function": {
                                            "name": "",
                                            "arguments": ""
                                        }
                                    }

                                # Update function name if present
                                if "function" in tool_call and "name" in tool_call["function"]:
                                    tool_call_data[call_index]["function"]["name"] = tool_call["function"]["name"]

                                # Accumulate arguments if present
                                if "function" in tool_call and "arguments" in tool_call["function"]:
                                    args_chunk = tool_call["function"]["arguments"]
                                    tool_call_data[call_index]["function"]["arguments"] += args_chunk

                        except json.JSONDecodeError as e:
                            print(f"TRACE: Error parsing JSON chunk: {e}")
                            continue

                    print(f"TRACE: Accumulated tool call data: {tool_call_data}")

                    # Convert accumulated data to final tool calls
                    for call_index, call_data in tool_call_data.items():
                        function_name = call_data["function"]["name"]
                        if "." in function_name:  # MCP function
                            arguments_str = call_data["function"]["arguments"]
                            print(f"TRACE: Processing function {function_name} with accumulated args: '{arguments_str}'")

                            try:
                                # Try to parse the accumulated arguments as JSON
                                if arguments_str.strip():
                                    arguments = json.loads(arguments_str)
                                else:
                                    arguments = {}
                            except json.JSONDecodeError:
                                print(f"TRACE: Failed to parse arguments as JSON, trying fallback")
                                # Fallback: extract path from the user message if it's a file read
                                if "read_file" in function_name:
                                    # Look for file path in the original user messages
                                    path_found = None
                                    for message in payload.get("messages", []):
                                        if message.get("role") == "user":
                                            content = message.get("content", "")
                                            path_match = re.search(r'/[\w/.]+', content)
                                            if path_match:
                                                path_found = path_match.group(0)
                                                break

                                    arguments = {"path": path_found or "/tmp/test.txt"}
                                else:
                                    arguments = {}

                            tool_calls.append({
                                "type": "function",
                                "function": {
                                    "name": function_name,
                                    "arguments": arguments
                                }
                            })
                            print(f"TRACE: Added tool call: {function_name} with args: {arguments}")

                elif isinstance(response, dict):
                    # Handle structured response format
                    if "choices" in response:
                        choice = response["choices"][0] if response["choices"] else {}
                        message = choice.get("message", {})
                        tool_calls = message.get("tool_calls", [])
                    elif "tool_calls" in response:
                        tool_calls = response["tool_calls"]

                print(f"TRACE: Tool calls found: {len(tool_calls)}")
                if tool_calls:
                    print(f"TRACE: First tool call: {tool_calls[0] if tool_calls else 'None'}")
                    print(f"TRACE: About to start processing {len(tool_calls)} function calls")
                    logger.info(f"Processing {len(tool_calls)} function calls")

                    # Execute MCP function calls
                    print(f"TRACE: Starting loop through {len(tool_calls)} tool calls")
                    for tool_call in tool_calls:
                        print(f"TRACE: Processing tool call: {tool_call}")
                        if tool_call.get("type") == "function":
                            function = tool_call.get("function", {})
                            function_name = function.get("name", "")
                            print(f"TRACE: Function name: {function_name}")

                            # Check if this is an MCP function (contains server prefix)
                            if "." in function_name:
                                print(f"TRACE: MCP function detected: {function_name}")
                                try:
                                    arguments = function.get("arguments", {})
                                    if isinstance(arguments, str):
                                        arguments = json.loads(arguments)

                                    print(f"TRACE: About to execute MCP function with args: {arguments}")
                                    # Execute MCP function
                                    mcp_result = await execute_mcp_function_call(function_name, arguments)
                                    print(f"TRACE: MCP function executed, result: {mcp_result}")
                                    mcp_function_results.append(mcp_result)

                                except Exception as e:
                                    logger.error(f"Error executing MCP function {function_name}: {e}")
                                    mcp_function_results.append({
                                        "success": False,
                                        "error": str(e),
                                        "function_name": function_name
                                    })

                    # If we have MCP function results, inject them into the conversation and get another response
                    print(f"TRACE: MCP function results count: {len(mcp_function_results)}")
                    if mcp_function_results:
                        print(f"TRACE: About to inject MCP function results")
                        logger.info("Injecting MCP function results and getting final response")

                        # Inject MCP metadata into the original streaming response BEFORE the second call
                        print(f"TRACE: MCP execution completed: {len(mcp_function_results)} function calls, {len(mcp_tools)} tools available")

                        # Prepare MCP metadata for frontend
                        mcp_tools_data = []
                        mcp_status_data = []

                        for result in mcp_function_results:
                            function_name = result.get('function_name', 'unknown')
                            server_name = function_name.split('.')[0] if '.' in function_name else 'unknown'
                            tool_name = function_name.split('.')[1] if '.' in function_name else function_name

                            mcp_tools_data.append({
                                'name': tool_name,
                                'server': server_name,
                                'qualified_name': function_name,
                                'description': f"MCP tool: {tool_name}",
                                'category': 'file_operations' if 'file' in tool_name.lower() else 'utility'
                            })

                            mcp_status_data.append({
                                'status': 'completed' if result.get('success') else 'failed',
                                'tool_name': tool_name,
                                'server': server_name,
                                'start_time': result.get('start_time'),
                                'end_time': result.get('end_time'),
                                'message': result.get('error') if not result.get('success') else 'Executed successfully'
                            })

                        # Inject MCP metadata into the original streaming response
                        print(f"TRACE: Checking MCP injection - Response exists: {bool(response)}")
                        print(f"TRACE: Response contains [DONE]: {'data: [DONE]' in response if response else False}")

                        if response and 'data: [DONE]' in response:
                            # Insert MCP metadata before the [DONE] marker
                            mcp_metadata = {
                                "mcpTools": mcp_tools_data,
                                "mcpStatus": mcp_status_data
                            }

                            mcp_data_line = f'data: {{"mcp": {json.dumps(mcp_metadata)}, "s": "mcp_metadata"}}\n\n'
                            response = response.replace('data: [DONE]', mcp_data_line + 'data: [DONE]')

                            print(f"TRACE: Injected MCP metadata into streaming response: {len(mcp_tools_data)} tools")
                        else:
                            print(f"TRACE: MCP injection skipped - Response preview: {response[:200] if response else 'No response'}")

                        # Add function results to messages
                        print(f"TRACE: About to call process_mcp_function_calls with {len(mcp_function_results)} results")
                        for i, result in enumerate(mcp_function_results):
                            print(f"TRACE: MCP result {i}: success={result.get('success')}, content={result.get('content', 'No content')[:100]}")

                        enhanced_messages = process_mcp_function_calls(payload["messages"], mcp_function_results)
                        print(f"TRACE: Enhanced messages created")
                        print(f"TRACE: Original messages count: {len(payload['messages'])}")
                        print(f"TRACE: Enhanced messages count: {len(enhanced_messages)}")

                        # Show what was added
                        if len(enhanced_messages) > len(payload["messages"]):
                            added_messages = enhanced_messages[len(payload["messages"]):]
                            for i, msg in enumerate(added_messages):
                                print(f"TRACE: Added message {i}: role={msg.get('role')}, content={msg.get('content', '')[:100]}")

                        # Create a new payload for the final response
                        final_payload = payload.copy()
                        final_payload["messages"] = enhanced_messages

                        # Remove tools from final request to avoid recursive function calls
                        final_payload.pop("tools", None)
                        final_payload.pop("tool_choice", None)

                        # Get final response with MCP results included
                        final_response, metadata = chat(chat_endpoint_url, access_token, final_payload)
                        print(f"TRACE: Final response generated")

                        # Always use the final response when we have MCP function results
                        # The raw endpoint will convert it to SSE format later
                        if final_response:
                            print(f"TRACE: Using final response with MCP results instead of raw tool calls")
                            response = final_response

                            # Store MCP metadata globally for the raw endpoint to inject
                            current_mcp_metadata = {
                                "mcpTools": mcp_tools_data,
                                "mcpStatus": mcp_status_data
                            }
                            print(f"TRACE: Stored MCP metadata globally for raw endpoint: {len(mcp_tools_data)} tools")
                        else:
                            print(f"TRACE: No final response generated, keeping original response")
            else:
                print("TRACE: NOT entering MCP tool call processing")
                print(f"TRACE: Conditions - MCP initialized: {mcp_initialized}, Response exists: {bool(response)}")


            print("TRACE: About to return successful response")
            print(f"TRACE: Response data length: {len(str(response)) if response else 0}")

            # Return the response with MCP metadata for frontend compatibility
            print("TRACE: Returning streaming response with MCP metadata for frontend compatibility")
            return response

        finally:
            # Clean up MCP resources
            if mcp_initialized:
                await cleanup_mcp_integration()

    except Exception as e:
        import traceback
        print(f"TRACE: Exception caught in async_chat_endpoint: {e}")
        print(f"TRACE: Exception type: {type(e)}")
        print(f"TRACE: async_chat_endpoint traceback: {traceback.format_exc()}")
        logger.error(f"TRACE: Exception in async_chat_endpoint: {e}")
        logger.error(f"TRACE: async_chat_endpoint traceback: {traceback.format_exc()}")

        # Clean up MCP resources in case of error
        try:
            await cleanup_mcp_integration()
        except:
            pass

        error_msg = e
        if isinstance(error_msg, dict):
            error_msg = json.dumps(error_msg)
        elif not isinstance(error_msg, str):
            error_msg = str(error_msg)

        return {"success": False, "message": f"Chat service error: {error_msg}"}


def convert_decimal(obj):
    """Convert Decimal objects to Python native types (float or int)"""
    if isinstance(obj, Decimal):
        return float(obj) if obj % 1 != 0 else int(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal(item) for item in obj]
    return obj


def get_data_source_details(data_sources):
    if len(data_sources) == 0:
        return []

    table_name = os.environ[
        "FILES_DYNAMO_TABLE"
    ]  # Get the table name from the environment variable
    dynamodb = boto3.resource("dynamodb")
    data_source_ids = []

    for data_source in data_sources:
        id = None
        if isinstance(data_source, str):
            id = data_source
        elif isinstance(data_source, dict) and "id" in data_source:
            id = data_source["id"]

        if id:
            if id.startswith("s3://"):
                id = id.split("s3://")[1]
            data_source_ids.append(id)

    # Properly format batch_get_item request
    response = dynamodb.batch_get_item(
        RequestItems={
            table_name: {
                "Keys": [{"id": collection_id} for collection_id in data_source_ids]
            }
        }
    )

    # Format the response items as required
    formatted_sources = []
    found_ids = set()
    if "Responses" in response and table_name in response["Responses"]:
        items = response["Responses"][table_name]
        for item in items:
            found_ids.add(item.get("id", ""))
            # Create metadata object
            metadata = {
                "createdAt": item.get("createdAt", ""),
                "tags": item.get("tags", []),
                "totalTokens": item.get("totalTokens", 0),
            }
            id = item.get("id", "")
            # Ensure id is a string (DynamoDB might return complex types)
            if isinstance(id, dict):
                id = str(id)
            elif not isinstance(id, str):
                id = str(id) if id else ""

            # Create formatted object
            formatted_item = {
                "id": "s3://" + id,
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "data": item.get("data", ""),
                "metadata": metadata,
                "key": id,
            }
            formatted_sources.append(formatted_item)

    # Optional: track missing items
    missing_ids = set(data_source_ids) - found_ids
    if missing_ids:
        print(f"Warning: The following requested IDs were not found: {missing_ids}")

    # Convert any Decimal objects to regular Python types
    formatted_sources = convert_decimal(formatted_sources)

    return formatted_sources


def validate_assistant_id(assistant_id, access_token):
    print("Initiate call to validate assistant id: ", assistant_id)
    api_base_url = os.environ["API_BASE_URL"]
    # Ensure API_BASE_URL is a string (environment variables can sometimes be dicts)
    if isinstance(api_base_url, dict):
        api_base_url = str(api_base_url)
    elif not isinstance(api_base_url, str):
        api_base_url = str(api_base_url) if api_base_url else ""
    endpoint = api_base_url + "/assistant/validate/assistant_id"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            data=json.dumps({"data": {"assistantId": assistant_id}}),
        )
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200:
            print("Error validating assistant id: ", response.content)
            return {"success": False}
        return response_content

    except Exception as e:
        print(f"Error validating assistant id: {e}")
        return {"success": False}
