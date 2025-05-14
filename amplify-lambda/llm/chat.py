import requests
import json

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

    try:
        # invoke the chat_streaming function with the provided parameters
        chat_streaming(chat_url, access_token, payload, content_handler, meta_handler)
        return concatenated_d_data, meta_events
    except Exception as e:
        # Return the error message in a format that can be handled by the caller
        error_msg = str(e)
        print(f"Error in chat function: {error_msg}")
        return f"Error: {error_msg}", []


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
        try:
            # Try to extract error message from response body
            error_content = response.json()
            error_message = error_content.get('error')
            
            if error_message:
                # Return a more descriptive error with the actual message
                print(f"Request failed with status {response.status_code}: {error_message}")
                raise Exception(f"Request failed with status {response.status_code}: {error_message}")
            else:
                # Fallback to standard status code error
                response.raise_for_status()
        except json.JSONDecodeError:
            # If response is not valid JSON, fall back to standard error
            response.raise_for_status()

    error_message = None

    # Process the streamed response
    for line in response.iter_lines():
        if line:
            try:
                # Remove 'data: ' prefix if present
                stripped_line = line.decode('utf-8').lstrip("data: ").strip()
                if stripped_line:
                    data = json.loads(stripped_line)
                    # Check for error in the response
                    if "error" in data:
                        error_message = data["error"]
                        print(f"Error detected from chat service: {error_message}")
                        # Break the loop when error is detected
                        break
                    
                    if data.get("s") == "meta":
                        meta_handler(data)
                    else:
                        content_handler(data)
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e} - Content: {line}")
                continue
    
    # If we found an error, raise an exception to propagate it back
    if error_message:
        raise Exception(f"{error_message}")


