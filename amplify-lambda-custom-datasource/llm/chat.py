import mimetypes
import requests
import json
import os


def tag_file(api_url, file_key, access_token, tags):
    data = {
        "data": {
            "id": file_key,
            "tags": tags
        }
    }
    headers = {
        'Accept': '*/*',
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    response = requests.post(api_url+'/assistant/files/set_tags', headers=headers, data=json.dumps(data))
    response.raise_for_status()  # Raise an exception for HTTP errors
    result = response.json()
    return result


def upload_file(api_url, access_token, file_path, tags=[]):
    # Extract file name from file path
    file_name = os.path.basename(file_path)
    content_type, _ = mimetypes.guess_type(file_path)

    if content_type is None:
        content_type = 'application/octet-stream'

    # First step: Get the presigned URL
    headers = {
        'Accept': '*/*',
        'Authorization': f'Bearer {access_token}',
        'Content-Type': content_type
    }
    data = {
        "data": {
            "actions": [],
            "type": content_type,
            "name": file_name,
            "knowledgeBase": "default",
            "tags": tags,
            "data": {}
        }
    }

    response = requests.post(api_url+'/assistant/files/upload', headers=headers, data=json.dumps(data))
    response.raise_for_status()  # Raise an exception for HTTP errors
    presigned_data = response.json()

    # Extract the presigned URL
    presigned_url = presigned_data['uploadUrl']

    # Second step: Upload the file to the presigned URL
    with open(file_path, 'rb') as file:
        headers = {
            'Accept': '*/*',
            'Content-Type': content_type
        }
        upload_response = requests.put(presigned_url, data=file, headers=headers)
        upload_response.raise_for_status()  # Raise an exception for HTTP errors

    # Extract the key from the response JSON
    file_key = presigned_data.get('key')

    # Tag the file
    if tags:
        tag_file(api_url, file_key, access_token, tags)

    return presigned_data


def chat(chat_url, access_token, payload):
    """
    Invoke the specified endpoint with a JSON payload and handle the streamed response.

    This function sends a POST request to the given URL, using the provided access token
    for authorization. The payload is a JSON object that includes various configurations
    like model, temperature, max_tokens, and messages. The response is streamed and parsed
    for "meta" events which are accumulated into a list, and other events where the "d"
    value is concatenated into a single string.

    Args:
        url (str): The endpoint URL to send the POST request to.
        access_token (str): The access token used for authorization (Bearer token).
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
                    "role": "user",
                    "content": "Say hello in three words.",
                    "type": "prompt",
                    "data": {},
                    "id": "example-id-1234"
                }
            ],
            "options": {
                "requestId": "example-request-id",
                "model": {
                    "id": "gpt-4-1106-Preview",
                },
                "prompt": "Follow the user's instructions carefully. Respond using markdown. Be concise in your responses unless told otherwise.",
                "ragOnly": True,
            }
    }

    Example usage:
        url = 'https://your-api-endpoint.com/path'
        access_token = 'your_access_token_here'
        result, meta_events = chat(url, access_token, payload)
        print("Meta Events:", meta_events)
        print("Concatenated Data:", result)

        :param chat_url:
        :param payload:
        :param access_token:

    """
    concatenated_d_data = ""
    meta_events = []

    # create a content handler that appends the "d" value to the concatenated_d_data
    def content_handler(data):
        nonlocal concatenated_d_data
        concatenated_d_data += data.get("d", "")

    # create a meta handler that appends the meta event to the meta_events list
    def meta_handler(data):
        nonlocal meta_events
        meta_events.append(data)

    # invoke the chat_streaming function with the provided parameters
    chat_streaming(chat_url, access_token, payload, content_handler, meta_handler)

    return concatenated_d_data, meta_events


def chat_streaming(chat_url, access_token, payload, content_handler, meta_handler = lambda x: None):
    """
    Invoke the specified endpoint with a JSON payload and handle the streamed response
    by providing the streamed events to the content_handler and meta_handler.

    This function sends a POST request to the given URL, using the provided access token
    for authorization. The payload is a JSON object that includes various configurations
    like model, temperature, max_tokens, and messages.

    Args:
        url (str): The endpoint URL to send the POST request to.
        access_token (str): The access token used for authorization (Bearer token).
        payload (dict): The JSON payload containing the request parameters.
        content_handler (function): A function that handles the content events.
        meta_handler (function): A function that handles the meta events.

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
                    "role": "user",
                    "content": "Say hello in three words.",
                    "type": "prompt",
                    "data": {},
                    "id": "example-id-1234"
                }
            ],
            "options": {
                "requestId": "example-request-id",
                "model": {
                    "id": "gpt-4-1106-Preview",
                },
                "prompt": "Follow the user's instructions carefully. Respond using markdown. Be concise in your responses unless told otherwise.",
                "ragOnly": True,
            }
    }

    Example usage:
        url = 'https://your-api-endpoint.com/path'
        access_token = 'your_access_token_here'
        chat_streaming(url, access_token, payload, lambda x: print(x), lambda x: print(x))

        :param chat_url:
        :param payload:
        :param access_token:
        :param meta_handler:
        :param content_handler:

    """
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Send POST request to the specified URL
    response = requests.post(chat_url, headers=headers, json=payload, stream=True)

    if response.status_code != 200:
        response.raise_for_status()

    # Process the streamed response
    for line in response.iter_lines():
        if line:
            try:
                # Remove 'data: ' prefix if present
                stripped_line = line.decode('utf-8').lstrip("data: ").strip()
                if stripped_line:
                    data = json.loads(stripped_line)
                    if data.get("s") == "meta":
                        meta_handler(data)
                    else:
                        content_handler(data)
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e} - Content: {line}")
                continue


