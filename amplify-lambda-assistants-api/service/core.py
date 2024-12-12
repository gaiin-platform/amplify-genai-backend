from common.validate import validated
import requests
import json


def build_http_action(current_user, data):
    # Extract request details
    method = data["RequestType"]
    url = data["URL"]
    params = data.get("Parameters", {})
    body = data.get("Body", {})
    headers = data.get("Headers", {})
    auth = data.get("Auth", {})

    # Set up authentication if provided
    auth_instance = None
    if auth:
        if auth["type"].lower() == "bearer":
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth["type"].lower() == "basic":
            auth_instance = requests.auth.HTTPBasicAuth(
                auth["username"], auth["password"]
            )

    def action():
        # Make the request
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=body if body else None,
            headers=headers,
            auth=auth_instance,
        )

        if response.status_code == 200:
            return response.status_code, response.reason, response.json()

        return response.status_code, response.reason, None

    return action

def build_action(current_user, token, data):
    #return build_http_action(current_user, data)
    return lambda : (200, "Operation complete.", {"data": "success"})

@validated("execute_custom_auto")
def execute_custom_auto(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        token = data["access_token"]
        nested_data = data["data"]

        # Print the nested data neatly as a dictionary
        print("Nested data:")
        print(json.dumps(nested_data, indent=2))

        conversation_id = nested_data["conversation"]
        message_id = nested_data["message"]
        assistant_id = nested_data["assistant"]

        # Log the conversation and message IDs
        print(f"Conversation ID: {conversation_id}")
        print(f"Message ID: {message_id}")
        print(f"Assistant ID: {assistant_id}")

        action = build_action(current_user, token, nested_data)

        if action is None:
            return 404, "The specified operation was not found. Double check the name and ID of the action.", None

        code, message, result = action()

        # Return the response content
        return {
            'success': True,
            'data': {
                'code': code,
                'message': message,
                'result': result
            }
        }

    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"
