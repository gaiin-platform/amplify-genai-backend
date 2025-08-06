from decimal import Decimal
from requests.auth import HTTPBasicAuth
from pycommon.encoders import SafeDecimalEncoder

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_permissions_by_state

set_permissions_by_state(permissions)

import json
import requests
import os
import boto3
from datetime import datetime
import re
import urllib.parse
from service.jobs import check_job_status, set_job_result

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["OP_LOG_DYNAMO_TABLE"])


def log_execution(current_user, data, code, message, result, metadata={}):
    try:
        if not os.environ.get("OP_TRACING_ENABLED", "false").lower() == "true":
            return

        timestamp = datetime.utcnow().isoformat()

        # If there is metadata start_time, use it as the timestamp
        if "start_time" in metadata:
            timestamp = metadata["start_time"]
            # convert it to the right format
            timestamp = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        log_item = json.loads(
            json.dumps(
                {
                    "user": current_user,
                    "timestamp": timestamp,
                    "metadata": metadata,
                    "conversationId": data["conversation"],
                    "messageId": data["message"],
                    "assistantId": data.get("assistant", "chat"),
                    "actionName": data["action"]["name"],
                    "resultCode": code,
                    "resultMessage": message,
                    "operationDefinition": data["operationDefinition"],
                    "actionPayload": (
                        data["action"].get("payload", {})
                        if os.environ.get(
                            "OP_TRACING_REQUEST_DETAILS_ENABLED", "false"
                        ).lower()
                        == "true"
                        else None
                    ),
                    "result": (
                        result
                        if os.environ.get(
                            "OP_TRACING_RESULT_DETAILS_ENABLED", "false"
                        ).lower()
                        == "true"
                        else None
                    ),
                },
                cls=SafeDecimalEncoder,
            ),
            parse_float=Decimal,
        )

        log_item = {k: v for k, v in log_item.items() if v is not None}

        # We have to make sure that we stay in the size limits of DynamoDB rows
        item_size = len(json.dumps(log_item, cls=SafeDecimalEncoder))
        if item_size > 400000:
            for key in ["result", "actionPayload", "operationDefinition"]:
                if key in log_item:
                    del log_item[key]
                    if len(json.dumps(log_item, cls=SafeDecimalEncoder)) <= 400000:
                        break

        table.put_item(Item=log_item)
    except Exception as e:
        print(f"Error logging execution: {str(e)}")


def build_amplify_api_action(current_user, token, data, method="POST"):
    base_url = os.environ.get("API_BASE_URL", None)
    if not base_url:
        raise ValueError("API_BASE_URL environment variable is not set")

    endpoint = data["operationDefinition"]["url"]
    url = f"{base_url}{endpoint}"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = data["action"].get("payload", {})

    def send_request():
        if method.upper() == "GET":
            print(f"Sending GET request to {url} with query params: {payload}")
            response = requests.get(url, headers=headers, params=payload)
        else:
            print(f"Sending POST request to {url} with payload: {payload}")
            response = requests.post(
                url, headers=headers, data=json.dumps({"data": payload})
            )

        response.raise_for_status()

        result = None
        if response.status_code == 200:
            result = response.json()

        return response.status_code, response.reason, result

    return send_request


def print_curl_command(method, url, headers, body, auth_instance):
    curl_command = f'curl -X {method} "{url}"'

    for key, value in headers.items():
        curl_command += f' -H "{key}: {value}"'

    if body:
        body_json = json.dumps(body).replace('"', '\\"')
        curl_command += f' -d "{body_json}"'

    if isinstance(auth_instance, HTTPBasicAuth):
        curl_command += f' -u "{auth_instance.username}:{auth_instance.password}"'

    print("cURL command:")
    print(curl_command)


def replace_placeholders(
    value, payload, url_encode=False, escape_quotes=False, escape_newlines=False
):
    def replace(match):
        key = match.group(1)
        replacement = str(payload.get(key, match.group(0)))
        if url_encode:
            replacement = urllib.parse.quote(replacement)
        if escape_quotes:
            replacement = replacement.replace('"', '\\"')
        if escape_newlines:
            replacement = replacement.replace("\n", "\\n").replace("\r", "\\r")
        return replacement

    if isinstance(value, str):
        return re.sub(r"\${(\w+)}", replace, value)
    elif isinstance(value, dict):
        return {
            k: replace_placeholders(
                v, payload, url_encode, escape_quotes, escape_newlines
            )
            for k, v in value.items()
        }
    return value


def build_http_action(current_user, data):
    # Extract request details

    action = data.get("action", {})
    payload = action.get("payload", {})
    operation_definition = data.get("operationDefinition", {})
    url = replace_placeholders(
        operation_definition.get("url", ""), payload, url_encode=True
    )
    method = operation_definition.get("requestType", "GET")
    headers = replace_placeholders(
        operation_definition.get("headers", {}), payload, escape_newlines=True
    )
    body = replace_placeholders(
        operation_definition.get("body", ""), payload, escape_quotes=True
    )
    auth = operation_definition.get("auth", None)

    # print everything
    print(f"Operation definition: {operation_definition}")
    print(f"Action: {action}")
    print(f"Building HTTP action for URL: {url}")
    print(f"Method: {method}")
    print(f"Body: {body}")
    print(f"Headers: {headers}")
    print(f"Auth: {auth}")

    # Set up authentication if provided
    auth_instance = None
    if auth:
        if auth["type"].lower() == "bearer":
            print("Setting up bearer token authentication.")
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth["type"].lower() == "basic":
            auth_instance = HTTPBasicAuth(auth["username"], auth["password"])

    def action():

        # print everything going into the request in a nice format similar to the function call
        debug = True
        if debug:
            print(f"Final HTTP action for URL: {url}")
            print(f"Method: {method}")
            print(f"Body: {body}")
            print(f"Headers: {headers}")
            print(f"Auth: {auth_instance}")
            print_curl_command(method, url, headers, body, auth_instance)

        # Make the request
        response = requests.request(
            method=method,
            url=url,
            json=body if body and method != "GET" and method != "HEAD" else None,
            headers=headers,
            auth=auth_instance,
        )

        if response.status_code == 200:
            return response.status_code, response.reason, response.json()

        print(f"HTTP request failed with status code {response.status_code} and reason {response.reason}")
        return response.status_code, response.reason, None

    return action


def resolve_op_definition(current_user, token, action_name, data):
    op_def = data.get("operationDefinition", None)
    if not op_def:
        print(f"Operation definition not found in data, resolving...")

        api_base = os.environ.get("API_BASE_URL", None)
        # make a call to API_BASE_URL + /ops/get with {data:{tag:default}} as the payload and the token as a
        # a bearer token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"data": {"tag": "default"}}
        try:
            response = requests.post(
                f"{api_base}/ops/get", headers=headers, data=json.dumps(payload)
            )
            response.raise_for_status()
            result = response.json()

            print(f"Result: {result}")
            # convert to dict
            ops = result.get("data", [])
            print(f"Ops: {ops}")
            # find the operation definition with the name action_name
            op_def = next(
                (op for op in ops if op.get("name", None) == action_name), None
            )
        except Exception as e:
            print(f"Failed to resolve operation definition: {str(e)}")
            return None

    if op_def and not data.get("operationDefinition", None):
        data["operationDefinition"] = op_def

    print(f"Op def: {op_def}")
    return op_def


def build_action(current_user, token, action_name, data):
    # return build_http_action(current_user, data)
    op_def = resolve_op_definition(current_user, token, action_name, data)

    if not op_def:
        print(f"No operation definition found for {action_name}.")
        raise ValueError(f"No operation definition found for {action_name}.")

    action_type = op_def.get("type", "custom")

    if action_type != "http":
        print("Building Amplify API action.")
        return build_amplify_api_action(current_user, token, data, op_def.get("method", "POST"))
    else:
        print("Building HTTP action.")
        return build_http_action(current_user, data)

    print("Unknown operation type.")
    return lambda: (
        200,
        "Unknown operation type.",
        {"data": "Please double check the operation defintion."},
    )


@validated("execute_custom_auto")
def execute_custom_auto(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        token = data["access_token"]
        nested_data = data["data"]

        conversation_id = nested_data["conversation"]
        message_id = nested_data["message"]
        assistant_id = nested_data["assistant"]

        # Log the conversation and message IDs
        action_name = nested_data.get("action", {}).get("name", "unknown")
        print(f"Executing action: {action_name}")
        print(f"Payload keys: {nested_data.get('action', {}).get('payload', {}).keys()}")
        print(f"Conversation ID: {conversation_id}")
        print(f"Message ID: {message_id}")
        print(f"Assistant ID: {assistant_id}")

        action = build_action(current_user, token, action_name, nested_data)

        if action is None:
            print("The specified operation was not found.")
            return 404, "The specified operation was not found. Double check the name and ID of the action.", None

        try:
            # Log the execution time
            print("Executing action...")
            start_time = datetime.now()
            code, message, result = action()
            end_time = datetime.now()

            print(f"Execution time: {end_time - start_time}")

            # Create metadata that captures start_time and end_time in camel case and converts to isoformat
            metadata = {
                "startTime": start_time.isoformat(),
                "endTime": end_time.isoformat(),
                "executionTime": str(end_time - start_time),
            }

            log_execution(current_user, nested_data, code, message, result, metadata)

            # Return the response content
            return {
                "success": True,
                "data": {"code": code, "message": message, "result": result},
            }
        except Exception as e:
            error_result = {
                "success": False,
                "data": {
                    "code": 500,
                    "message": f"An unexpected error occurred: {str(e)}",
                    "result": None,
                },
            }
            log_execution(
                current_user,
                nested_data,
                500,
                f"An unexpected error occurred: {str(e)}",
                error_result,
            )

            print(f"An error occurred while executing the action: {str(e)}")
            return {"success": False, "error": str(e)}

    except Exception as e:
        error_result = {
            "success": False,
            "data": {
                "code": 500,
                "message": f"An unexpected error occurred: {str(e)}",
                "result": None,
            },
        }
        log_execution(
            current_user,
            data.get("data", {}),
            500,
            f"An unexpected error occurred: {str(e)}",
            error_result,
        )

        return f"An unexpected error occurred: {str(e)}"


@api_tool(
    path="/assistant-api/get-job-result",
    tags=["default"],
    name="getJobResult",
    description="Returns the status of the job and/or the result if it is finished.",
    parameters={
        "type": "object",
        "properties": {
            "jobId": {
                "type": "string",
                "description": "The job ID to fetch the result / status of.",
            }
        },
        "required": ["jobId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "oneOf": [
                    {
                        "type": "object",
                        "description": "Job result data when job is finished",
                    },
                    {
                        "type": "string",
                        "description": "Job status (running, finished, stopped) or error message (Job not found, Unknown state)",
                    },
                ],
                "description": "Job status, result data, or error message",
            },
            "error": {
                "type": "string",
                "description": "Error message when operation fails",
            },
        },
        "required": ["success"],
    },
)
@validated("get_result")
def get_job_result(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        token = data["access_token"]
        nested_data = data["data"]

        job_id = nested_data["jobId"]

        result = check_job_status(current_user, job_id)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@validated("set_result")
def update_job_result(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        nested_data = data["data"]

        job_id = nested_data["jobId"]
        result = nested_data["result"]
        store_in_s3 = nested_data.get("storeAsBlob", False)

        set_job_result(current_user, job_id, result, store_in_s3)

        return {"success": True, "data": {"message": "Job result updated."}}
    except Exception as e:
        return {"success": False, "error": str(e)}
