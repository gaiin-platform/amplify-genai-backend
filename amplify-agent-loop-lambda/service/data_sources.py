import json
import requests
from pycommon.api.get_endpoint import get_endpoint, EndpointType
from pycommon.logger import getLogger
logger = getLogger("data_sources")

def resolve_datasources(datasource_request, authorization_token=None, endpoint=None):
    """
    Resolves datasources by calling a configured resolver endpoint.

    Args:
        datasource_request (dict): The datasource request with the following structure:
            {
              "dataSources": [
                {
                  "id": "s3://user@domain.com/path/uuid.json",
                  "type": "application/json"
                }
              ],
              "options": {
                "useSignedUrls": true
              },
              "chat": {
                "messages": [ ... optional messages for context ... ]
              }
            }
        authorization_token (str, optional): Authorization token for the resolver endpoint
        endpoint (str, optional): The resolver endpoint URL, defaults to DATASOURCES_RESOLVER_ENDPOINT env var

    Returns:
        dict: The resolved datasources with signed URLs
    """
    
    resolver_endpoint = get_endpoint(EndpointType.CHAT_ENDPOINT)
    if not resolver_endpoint:
        raise ValueError(
            "No datasource resolver endpoint provided or configured in DATASOURCES_RESOLVER_ENDPOINT"
        )

    logger.info("Resolving datasources %s", json.dumps(datasource_request))

    headers = {"Content-Type": "application/json"}

    if authorization_token:
        headers["Authorization"] = f"Bearer {authorization_token}"

    try:
        response = requests.post(
            resolver_endpoint,
            headers=headers,
            json={"datasourceRequest": datasource_request},
        )

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error("Error resolving datasources: %s", e, exc_info=True)
        if hasattr(e, "response") and e.response:
            logger.error("Response: %s", e.response.text)
        return {"error": f"Failed to resolve datasources: {str(e)}", "dataSources": []}
