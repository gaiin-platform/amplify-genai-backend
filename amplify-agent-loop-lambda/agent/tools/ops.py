import json
import os
from typing import Optional
from uuid import uuid4
import requests

from agent.components.tool import get_tool_metadata, register_tool
from agent.core import ActionContext, Action, ActionRegistry
from pycommon.encoders import SmartDecimalEncoder

def register_op_actions(
    action_registry: ActionRegistry, access_token: str, current_user: str
):
    apis = get_all_apis(
        action_context=ActionContext(
            {
                "access_token": access_token,
                "current_user": current_user,
            }
        )
    )

    api_tools = ops_to_actions(apis)
    for action in api_tools:
        action_registry.register(action)


def ops_to_actions(apis):
    tools = ops_to_tools(apis)
    actions = []
    for tool in tools:
        actions.append(
            Action(
                name=tool["tool_name"],
                function=tool["function"],
                description=tool["description"],
                parameters=tool["parameters"],
                output=tool.get("output", {}),
                terminal=tool["terminal"],
            )
        )

    return actions


def ops_to_tools(apis):
    tools = []
    for api in apis:
        tool = op_to_tool(api)
        tools.append(tool)

    return tools


def get_ops_tools(action_context):
    apis = get_all_apis(action_context)
    return ops_to_tools(apis)


def get_default_ops_as_tools(token):
    api_base = os.environ.get("API_BASE_URL", None)
    # make a call to API_BASE_URL + /ops/get with {data:{tag:default}} as the payload and the token as a
    # a bearer token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"data": {"tag": "default"}}
    try:
        response = requests.post(
            f"{api_base}/ops/get", headers=headers, data=json.dumps(payload)
        )
        response.raise_for_status()
        result = response.json()

        # print(f"Result: {result}")
        # convert to dict
        ops = result.get("data", [])
        return ops_to_tools(ops)
    except Exception as e:
        print(f"Error getting default ops: {e}")
        return []


def build_schema_from_params(params):
    schema = {"type": "object", "properties": {}, "required": []}

    # Base case: return minimal schema if params is empty
    if not params:
        return schema

    for param in params:
        param_name = param["name"]
        description = param["description"]

        param_schema = {"description": description}

        # Set type based on description
        description_lower = description.lower()
        if "boolean" in description_lower:
            param_schema["type"] = "boolean"
        elif any(type_str in description_lower for type_str in ["str", "string"]):
            param_schema["type"] = "string"
        elif any(
            type_str in description_lower for type_str in ["int", "integer", "number"]
        ):
            param_schema["type"] = "number"
        elif "array" in description_lower or "list" in description_lower:
            param_schema["type"] = "array"
        elif "object" in description_lower or "dict" in description_lower:
            param_schema["type"] = "object"

        # Check if parameter is required
        if "required" in description_lower:
            schema["required"].append(param_name)

        schema["properties"][param_name] = param_schema

    return schema


def op_to_tool(api):
    name = api["name"]
    id = api["id"]
    tags = api.get("tags", [])
    desc = api.get("description", "")
    custom_description = api.get("customDescription", None)
    if custom_description and custom_description.strip():
        desc = custom_description.strip()
    custom_name = api.get("customName", None)

    # The bindings are in the format:
    # { "param_name": { "value": "value", "mode": "ai|manual" }, ... }
    # When mode is "ai", the value is a string that will be used to update the
    # the description of the parameter in the schema.
    #
    # If the mode is "manual", the value is the value that will be used in the API call.
    # However, we also remove manually bound parameters from the schema.
    #
    # Only parameters with bindings need to be updated. Otherwise, the schema and
    # invocation parameters will be used as is.
    bindings = api.get("bindings", {})

    parameters = api.get("parameters", {})  # Preference for schema

    # keeping for backwards compatibility with old ops
    schema = api.get("schema", {})  # backup schema
    params = api.get("params", [])  # backup backup schema

    # print("Building tool for API: ", api)

    # print("Bindings: ", bindings)

    def api_func_invoke(action_context: ActionContext, **kwargs) -> dict:
        print("Invoking API: ", id)
        # Ensure all manual bindings are present in kwargs
        for param_name, binding in bindings.items():
            if binding.get("mode") == "manual":
                print("Inserting manual binding for param: ", param_name)
                value = binding["value"]
                if isinstance(value, str) and value.lower() in ["true", "false"]:
                    value = value.lower() == "true"
                kwargs[param_name] = value

        return call_api(action_context=action_context, name=id, payload=kwargs)

    api_func = api_func_invoke

    op_schema = parameters or schema or build_schema_from_params(params)

    # Update schema parameter descriptions based on AI bindings
    for param_name, binding_info in bindings.items():
        if binding_info.get("mode") == "ai":
            new_description = binding_info.get("value")
            if (
                new_description
                and "properties" in op_schema
                and param_name in op_schema["properties"]
            ):
                print("Updating description for param: ", param_name)
                op_schema["properties"][param_name]["description"] = new_description

    # Remove parameters from schema if they have manual bindings
    if "properties" in op_schema:
        for param_name in list(op_schema["properties"].keys()):
            if param_name in bindings and bindings[param_name].get("mode") == "manual":
                print("Removing manually bound param from schema: ", param_name)
                op_schema["properties"].pop(param_name)
                if param_name in op_schema.get("required", []):
                    op_schema["required"].remove(param_name)

    tool_name = custom_name.strip() if custom_name and custom_name.strip() else id

    # print("Final tool name, description, and schema: ", tool_name, desc, op_schema)

    tool_metadata = get_tool_metadata(
        func=api_func,
        tool_name=id,
        description=desc,
        parameters_override=op_schema,
        terminal=False,
        tags=tags,
    )

    return tool_metadata


def get_all_apis(action_context: ActionContext) -> dict:
    return call_api(action_context=action_context, name="getOperations", payload={})


@register_tool(tags=["ops"])
def call_api(action_context: ActionContext, name: str, payload: dict) -> dict:
    # Extract parameters from action context
    params = {
        "access_token": action_context.get("access_token", None),
        "current_user": action_context.get("current_user", None),
        "conversation_id": action_context.get("session_id", str(uuid4())),
        "assistant_id": action_context.get("agent_id", str(uuid4())),
        "message_id": action_context.get("message_id", str(uuid4())),
    }

    # Check if the payload is empty
    print("============================================")
    print(f"Calling API: {name} ")
    print("============================================")

    return execute_api_call(name=name, payload=payload, **params)


def execute_api_call(
    name: str,
    payload: dict,
    access_token: Optional[str],
    current_user: Optional[str],
    conversation_id: Optional[str],
    assistant_id: Optional[str],
    message_id: Optional[str],
) -> dict:

    print(f"Executing api call for {current_user}\nConversation Id: {conversation_id}\nAssistant Id: {assistant_id}\nMessage Id: {message_id}")  

    try:
        conversation = ""
        message = ""
        assistant = ""

        path = "/assistant-api/execute-custom-auto"

        data = {
            "data": {
                "action": {"name": name, "payload": payload},
                "conversation": conversation,
                "message": message,
                "assistant": assistant,
            }
        }

        # Validate required parameters
        if not all([name, access_token]):
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": "Missing required parameters: path, payload, or token",
                    }
                ),
            }

        # Make the request
        result = send_post_request(path, data, access_token)

        print(f"Response: {result}")

        if (not result.get("success", False)):
            return result

        # Extract the response payload from the result
        response_payload = result.get("data", {})
        
        for _ in range(3):  # Limit the number of unwrapping attempts
            if isinstance(response_payload, dict):
                if response_payload.get("result"):
                    response_payload = response_payload["result"]
                elif response_payload.get("data"):
                    response_payload = response_payload["data"]
            else:
                break
        print(f"Response payload: {response_payload}")
        return response_payload

    except Exception as e:
        return {"success": False, "message": str(e)}


def send_post_request(path, payload, token):
    try:
        print(
            f"Sending POST request to {path} with payload: {payload} and token: {token}"
        )

        url_base = os.environ.get("API_BASE_URL")
        url = f"{url_base}{path}"

        print(f"URL: {url}")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = payload

        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()

        return response.json()

    except Exception as e:
        return {"success": False, "message": str(e)}