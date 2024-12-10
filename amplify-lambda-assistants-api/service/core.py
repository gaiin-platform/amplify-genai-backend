from common.validate import validated
import requests
import json


@validated("execute_custom_auto")
def execute_custom_auto(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        nested_data = data["data"]

        # Extract request details
        method = nested_data["RequestType"]
        url = nested_data["URL"]
        params = nested_data.get("Parameters", {})
        body = nested_data.get("Body", {})
        headers = nested_data.get("Headers", {})
        auth = nested_data.get("Auth", {})

        # Set up authentication if provided
        auth_instance = None
        if auth:
            if auth["type"].lower() == "bearer":
                headers["Authorization"] = f"Bearer {auth['token']}"
            elif auth["type"].lower() == "basic":
                auth_instance = requests.auth.HTTPBasicAuth(
                    auth["username"], auth["password"]
                )

        # Make the request
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=body if body else None,
            headers=headers,
            auth=auth_instance,
        )

        # Check if the request was successful
        response.raise_for_status()

        # Return the response content
        return {
            "statusCode": response.status_code,
            "body": (
                response.json()
                if "application/json" in response.headers.get("Content-Type", "")
                else response.text
            ),
        }

    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"
