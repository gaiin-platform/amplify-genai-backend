import os
import requests
import json


def refresh_integration_token(access_token, integration):
    print("Initiate refresh integration token call")

    refresh_endpoint = os.environ["API_BASE_URL"] + "/integrations/oauth/refresh_token"

    request = {"data": {"integration": integration}}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            refresh_endpoint, headers=headers, data=json.dumps(request)
        )
        print("Response: ", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return False
        elif response.status_code == 200 and response_content.get("success", False):
            return True

    except Exception as e:
        print(f"Error refreshing integration token: {e}")
        return False


def get_user_integrations(access_token):
    print("Initiate get user integrations call")

    endpoint = os.environ["API_BASE_URL"] + "/integrations/oauth/user/list"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.get(
            endpoint,
            headers=headers,
        )
        print("Response: ", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return None
        elif response.status_code == 200 and response_content.get("success", False):
            return response_content.get("data", None)

    except Exception as e:
        print(f"Error getting user integrations: {e}")
        return None
