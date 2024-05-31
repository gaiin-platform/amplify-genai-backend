import mimetypes

import requests
import json
import os

def upload_file(upload_url, accessToken, file_path):
    # Extract file name from file path
    file_name = os.path.basename(file_path)
    content_type, _ = mimetypes.guess_type(file_path)

    # First step: Get the presigned URL
    headers = {
        'Accept': '*/*',
        'Authorization': f'Bearer {accessToken}',
        'Content-Type': content_type
    }
    data = {
        "data": {
            "actions": [],
            "type": "application/pdf",
            "name": file_name,
            "knowledgeBase": "default",
            "tags": [],
            "data": {}
        }
    }

    response = requests.post(upload_url, headers=headers, data=json.dumps(data))
    response.raise_for_status()  # Raise an exception for HTTP errors
    presigned_data = response.json()

    print(presigned_data)

    # Extract the presigned URL
    presigned_url = presigned_data['uploadUrl']

    # Second step: Upload the file to the presigned URL
    with open(file_path, 'rb') as file:
        upload_response = requests.put(presigned_url, data=file, headers={'Content-Type': content_type})
        upload_response.raise_for_status()  # Raise an exception for HTTP errors

    # Extract the key from the response JSON
    file_key = presigned_data.get('key')

    return file_key


def do_chat(url, accessToken, payload):
    """
    Invoke the specified endpoint with a JSON payload and handle the streamed response.

    This function sends a POST request to the given URL, using the provided access token
    for authorization. The payload is a JSON object that includes various configurations
    like model, temperature, max_tokens, and messages. The response is streamed and parsed
    for "meta" events which are accumulated into a list, and other events where the "d"
    value is concatenated into a single string.

    Args:
        url (str): The endpoint URL to send the POST request to.
        accessToken (str): The access token used for authorization (Bearer token).
        payload (dict): The JSON payload containing the request parameters.

    Returns:
        tuple: A tuple containing the concatenated string of "d" values and a list of "meta" events.

    Example payload:
        payload = {
            "model": "gpt-4-1106-Preview",
            "temperature": 1,
            "max_tokens": 1000,
            "stream": True,
            "dataSources": [],
            "messages": [
                {
                    "role": "system",
                    "content": "Follow the user's instructions carefully. Respond using markdown. If you are asked to draw a diagram, you can use Mermaid diagrams using mermaid.js syntax in a ```mermaid code block. If you are asked to visualize something, you can use a ```vega code block with Vega-lite. Don't draw a diagram or visualize anything unless explicitly asked to do so. Be concise in your responses unless told otherwise."
                },
                {
                    "role": "user",
                    "content": "Tell me about the change management course.",
                    "type": "prompt",
                    "data": {
                        "assistant": {
                            "definition": {
                                "assistantId": "example-assistant-id",
                                "name": "Cloud Engineer"
                            }
                        }
                    },
                    "id": "example-id-1234"
                }
            ],
            "options": {
                "requestId": "example-request-id",
                "model": {
                    "id": "gpt-4-1106-Preview",
                    "name": "GPT-4-Turbo (Azure)",
                    "maxLength": 24000,
                    "tokenLimit": 8000,
                    "actualTokenLimit": 128000,
                    "inputCost": 0.01,
                    "outputCost": 0.03,
                    "description": "Consider for complex tasks requiring advanced understanding.\nOffers further advanced intelligence over its predecessors.\nCan carry out complex mathematical operations, code assistance, analyze intricate documents and datasets, demonstrates critical thinking, and in-depth context understanding.\nTrained on information available through April 2023."
                },
                "prompt": "Follow the user's instructions carefully. Respond using markdown. If you are asked to draw a diagram, you can use Mermaid diagrams using mermaid.js syntax in a ```mermaid code block. If you are asked to visualize something, you can use a ```vega code block with Vega-lite. Don't draw a diagram or visualize anything unless explicitly asked to do so. Be concise in your responses unless told otherwise.",
                "maxTokens": 1000,
                "ragOnly": True,
                "accountId": "example-account-id"
            }
        }

    Example usage:
        url = 'https://your-api-endpoint.com/path'
        access_token = 'your_access_token_here'
        result, meta_events = invoke_endpoint(url, access_token, payload)
        print("Concatenated Data:", result)
        print("Meta Events:", meta_events)
    """
    headers = {
        'Authorization': f'Bearer {accessToken}',
        'Content-Type': 'application/json'
    }

    # Send POST request to the specified URL
    response = requests.post(url, headers=headers, json=payload, stream=True)

    if response.status_code != 200:
        response.raise_for_status()

    concatenated_d_data = ""
    meta_events = []

    # Process the streamed response
    for line in response.iter_lines():
        if line:
            try:
                # Remove 'data: ' prefix if present
                stripped_line = line.decode('utf-8').lstrip("data: ").strip()
                if stripped_line:
                    data = json.loads(stripped_line)
                    if data.get("s") == "meta":
                        meta_events.append(data)
                    else:
                        concatenated_d_data += data.get("d", "")
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e} - Content: {line}")
                continue

    return concatenated_d_data, meta_events

