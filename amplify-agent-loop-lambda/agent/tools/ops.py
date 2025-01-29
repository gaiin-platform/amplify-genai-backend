import json
import os
from typing import Optional
from uuid import uuid4

import boto3
from aiohttp import payload_type

from agent.game.action import ActionContext
from agent.tool import register_tool, get_tool_metadata

def ops_to_tools(apis):
    tools = []
    for api in apis:
        tool = op_to_tool(api)
        tools.append(tool)

    return tools

def get_ops_tools(action_context):
    apis = get_all_apis(action_context)
    return ops_to_tools(apis)

def op_to_tool(api):
    name = api['name']
    id = api['id']
    tags = api.get('tags', [])
    desc = api.get('description', "")
    params = api.get('params', [])

    def api_func_invoke(action_context: ActionContext, payload: dict = {}) -> dict:
        return call_api(action_context=action_context, name=name, payload=payload)

    api_func = api_func_invoke

    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "description": f"The payload should contain the following keys: {json.dumps(params)}"
            }
        },
        "required": ["payload"]
    }

    tool_metadata = get_tool_metadata(
        func=api_func, tool_name=id, description=desc, args_override=schema, terminal=False, tags=tags
    )

    return tool_metadata


def get_all_apis(action_context: ActionContext) -> dict:
    return call_api(action_context=action_context, name='getOperations', payload={})


@register_tool(tags=['ops'])
def call_api(action_context: ActionContext, name: str, payload: dict) -> dict:
    # Extract parameters from action context
    params = {
        'access_token': action_context.get('access_token', None),
        'current_user': action_context.get('current_user', None),
        'conversation_id': action_context.get('session_id', str(uuid4())),
        'assistant_id': action_context.get('agent_id', str(uuid4())),
        'message_id': action_context.get('message_id', str(uuid4()))
    }

    return execute_api_call(
        name=name,
        payload=payload,
        **params
    )


def execute_api_call(
        name: str,
        payload: dict,
        access_token: Optional[str],
        current_user: Optional[str],
        conversation_id: Optional[str],
        assistant_id: Optional[str],
        message_id: Optional[str]
) -> dict:
    # Create a boto3 client for Lambda
    client = boto3.client('lambda')

    # Get the Lambda function name from the environment variable
    lambda_function_name = os.environ.get('OPS_LAMBDA_NAME')

    if not lambda_function_name:
        raise ValueError("OPS_LAMBDA_NAME environment variable is not set")

    print(f"Invoking Lambda function: {lambda_function_name}")

    # Prepare the payload for the Lambda invocation
    event = {
        'name': name,
        'payload': payload,
        'conversation': conversation_id,
        'message': message_id,
        'assistant': assistant_id,
        'token': access_token,
        'current_user': current_user
    }

    print(f"Payload: {event}")

    try:
        # Invoke the Lambda function
        response = client.invoke(
            FunctionName=lambda_function_name,
            InvocationType='RequestResponse',  # Wait for the response
            Payload=json.dumps(event)
        )

        print(f"Response: {response}")

        # Read the response
        response_payload = json.loads(response['Payload'].read())

        if response_payload.get('statusCode') != 200:
            # Print all the details of the error
            print(f"StatusCode: {response_payload.get('statusCode')}")
            print(f"Execution failed: {response_payload.get('body', '')}")

            raise Exception(f"Execution failed: {response_payload.get('body', '')}")

        response_payload = json.loads(response_payload['body'])

        for _ in range(3):  # Limit the number of unwrapping attempts
            if isinstance(response_payload, dict):
                if response_payload.get('result'):
                    response_payload = response_payload['result']
                elif response_payload.get('data'):
                    response_payload = response_payload['data']
            else:
                break

        return response_payload

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }