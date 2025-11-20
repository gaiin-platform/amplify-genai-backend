import json
import os
import requests


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
    resolver_endpoint = endpoint or os.environ.get("DATASOURCES_RESOLVER_ENDPOINT")
    if not resolver_endpoint:
        raise ValueError(
            "No datasource resolver endpoint provided or configured in DATASOURCES_RESOLVER_ENDPOINT"
        )

    print(f"Resolving datasources {json.dumps(datasource_request)}")

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
        import traceback

        traceback.print_exc()
        print(f"Error resolving datasources: {e}")
        if hasattr(e, "response") and e.response:
            print(f"Response: {e.response.text}")
        return {"error": f"Failed to resolve datasources: {str(e)}", "dataSources": []}
