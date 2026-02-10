import os
import requests
import json

from pycommon.logger import getLogger
logger = getLogger("admin_supported_models")


def get_supported_models(access_token):
    logger.info("Initiate get supported models call")

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
        logger.debug("Response: %s", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return {"success": False, "data": None}
        elif response.status_code == 200 and response_content.get("success", False):
            return response_content

    except Exception as e:
        logger.error("Error getting supported models: %s", e)
        return {"success": False, "data": None}


def update_supported_models(access_token, data):
    logger.info("Initiate update supported models call")

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
        logger.debug("Response: %s", response.content)
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
        logger.error("Error updating supported Models: %s", e)
        return {"success": False, "message": "Failed to make request"}
