import os
import requests
import json

from pycommon.logger import getLogger
logger = getLogger("office365_refresh_oauth")

def refresh_integration_token(access_token, integration):
    logger.info("Initiate refresh integration token call")

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
        logger.debug("Response: %s", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return False
        elif response.status_code == 200 and response_content.get("success", False):
            return True

    except Exception as e:
        logger.error("Error refreshing integration token: %s", e)
        return False


def get_user_integrations(access_token):
    logger.info("Initiate get user integrations call")

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
        logger.debug("Response: %s", response.content)
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success", False):
            return None
        elif response.status_code == 200 and response_content.get("success", False):
            return response_content.get("data", None)

    except Exception as e:
        logger.error("Error getting user integrations: %s", e)
        return None
