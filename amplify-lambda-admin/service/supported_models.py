import os
import requests
import json


def get_supported_models(access_token):
    print("Initiate get supported models call")

    update_model_endpoint = os.environ["API_BASE_URL"] + "/supported_models/get"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.get(
            update_model_endpoint,
            headers=headers,
        )
        print("Response: ", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return {"success": False, "data": None}
        elif response.status_code == 200 and response_content.get("success", False):
            return response_content

    except Exception as e:
        print(f"Error getting supported models: {e}")
        return {"success": False, "data": None}


def update_supported_models(access_token, data):
    print("Initiate update supported models call")

    update_model_endpoint = os.environ["API_BASE_URL"] + "/supported_models/update"

    request = {"data": data}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            update_model_endpoint, headers=headers, data=json.dumps(request)
        )
        print("Response: ", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return {
                "success": False,
                "message": response_content.get(
                    "message", "Failed to update supported models"
                ),
            }
        elif response.status_code == 200 and response_content.get("success", False):
            return response_content

    except Exception as e:
        print(f"Error updating supported Models: {e}")
        return {"success": False, "message": "Failed to make request"}
