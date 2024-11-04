import mimetypes
import uuid

import requests
import json
import os
from typing import Callable, Any, get_type_hints
from functools import wraps
from pydantic import BaseModel
import re
from functools import wraps
from typing import Callable, Any, Dict, Type
import yaml
from inspect import signature, Parameter, Signature


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
    response = requests.post(api_url+'/files/set_tags', headers=headers, data=json.dumps(data))
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

    response = requests.post(api_url+'/files/upload', headers=headers, data=json.dumps(data))
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


def extract_and_parse_yaml(input_string):
    # Regular expression to extract the YAML block
    yaml_pattern = re.compile(r'```yaml\s*(.*?)\s*```', re.DOTALL)
    match = yaml_pattern.search(input_string)

    if match:
        yaml_content = match.group(1)
        # Parse the YAML content into a dictionary
        parsed_dict = yaml.safe_load(yaml_content)
        return parsed_dict
    else:
        return None


def chat_llm(docstring: str,
             system_prompt: str, inputs: Dict[str, Any],
             input_instance: BaseModel,
             output_model: Type[BaseModel],
             chat_url: str,
             access_token: str,
             params: Dict[str, Any]={}) -> BaseModel:
    # Concatenate input variable names, their values, and their descriptions.
    input_descriptions = "\n".join(
        f"{field}: {getattr(input_instance, field)}" +
        (f" - {field_info.description}" if field_info.description else "")
        for field, field_info in input_instance.__class__.__fields__.items()
    )

    output_descriptions = "\n".join(
        f"{field}:" +
        (f" {field_info.description}" if field_info.description else "")
        for field, field_info in output_model.__fields__.items()
    )

    prompt_template = f"{system_prompt}\n{docstring}\nInputs:\n{input_descriptions}\nOutputs:\n{output_descriptions}"

    #print(f"Prompt Template: {prompt_template}")

    system_data_prompt = f"""
    Follow the user's instructions very carefully.
    Analyze the task or question and output the requested data.

    You output with the data should be in the YAML format:
    \`\`\`yaml
    thought: <INSERT THOUGHT>
    {output_descriptions}
    \`\`\`
    
    You MUST provide the requested data. Make sure strings are YAML multiline strings
    that properly escape special characters.
    
    You ALWAYS output a \`\`\`yaml code block.
    """

    messages = params.get("messages", [])
    messages.append(
        {
            "role": "system",
            "content": system_data_prompt,
        })
    messages.append(
        {
            "role": "user",
            "content": prompt_template,
            "type": "prompt",
            "data": {},
            "id": str(uuid.uuid4())
        })

    payload = {
        "model": params.get("model", os.environ.get('AMPLIFY_MODEL', 'gpt-4o')),
        "temperature": params.get("temperature", 1.0),
        "max_tokens": params.get("max_tokens", 1000),
        "stream": True,
        "dataSources": params.get("data_sources", []),
        "messages": messages,
        "options": {
            "requestId": params.get("request_id", str(uuid.uuid4())),
            "model": {
                "id": params.get("model", "gpt-4o"),
            },
            "prompt": system_data_prompt,
            "dataSourceOptions": {
                'insertConversationDocuments': params.get('insert_conversation_documents', False),
                'insertAttachedDocuments': params.get('insert_attached_documents', True),
                'ragConversationDocuments': params.get('rag_conversation_documents', True),
                'ragAttachedDocuments': params.get('rag_attached_documents', False),
                'insertConversationDocumentsMetadata': params.get('insert_conversation_documents_metadata', False),
                'insertAttachedDocumentsMetadata': params.get('insert_attached_documents_metadata', False),
            },
        }
    }

    # Create an output model instance with the generated prompt template.
    # return output_model(prompt_template=prompt_template)
    response, meta = chat(chat_url, access_token, payload)

    result = None
    try:
        output_data = extract_and_parse_yaml(response)
        result = output_model.parse_obj(output_data)
    except Exception as e:
        print(f"Error: {e}")
        print(f"Response: {response}")
        raise e

    return result


def prompt(system_prompt: str = ""):
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> BaseModel:
            # Get function signature
            sig = signature(func)

            new_params = [
                Parameter("chat_url", Parameter.KEYWORD_ONLY, annotation=str, default=os.getenv('AMPLIFY_CHAT_URL')),
                Parameter("access_token", Parameter.KEYWORD_ONLY, annotation=str, default=os.getenv('AMPLIFY_TOKEN')),
                Parameter("api_key", Parameter.KEYWORD_ONLY, annotation=str, default=os.getenv('AMPLIFY_API_KEY')),
                Parameter("system_prompt", Parameter.KEYWORD_ONLY, annotation=str, default=None),
                Parameter("insert_conversation_documents", Parameter.KEYWORD_ONLY, annotation=bool, default=False),
                Parameter("insert_attached_documents", Parameter.KEYWORD_ONLY, annotation=bool, default=True),
                Parameter("rag_conversation_documents", Parameter.KEYWORD_ONLY, annotation=bool, default=True),
                Parameter("rag_attached_documents", Parameter.KEYWORD_ONLY, annotation=bool, default=False),
                Parameter("insert_conversation_documents_metadata", Parameter.KEYWORD_ONLY, annotation=bool, default=False),
                Parameter("insert_attached_documents_metadata", Parameter.KEYWORD_ONLY, annotation=bool, default=False),
                Parameter("model", Parameter.KEYWORD_ONLY, annotation=str, default="gpt-4o"),
                Parameter("temperature", Parameter.KEYWORD_ONLY, annotation=float, default=1.0),
                Parameter("max_tokens", Parameter.KEYWORD_ONLY, annotation=int, default=1000),
                Parameter("stream", Parameter.KEYWORD_ONLY, annotation=bool, default=True),
                Parameter("data_sources", Parameter.KEYWORD_ONLY, annotation=list, default=[]),
                Parameter("messages", Parameter.KEYWORD_ONLY, annotation=list, default=None),
                Parameter("request_id", Parameter.KEYWORD_ONLY, annotation=str, default=str(uuid.uuid4())),
            ]

            updated_params = list(sig.parameters.values())
            for param in new_params:
                if param.name not in sig.parameters:
                    updated_params.append(param)

            # Create a new signature with the updated parameters
            new_sig = Signature(parameters=updated_params)
            func.__signature__ = new_sig

            # Bind arguments
            bound_args = new_sig.bind(*args, **kwargs)
            bound_args.apply_defaults()

            chat_url = kwargs.get('chat_url', os.getenv('AMPLIFY_CHAT_URL'))
            access_token = kwargs.get('access_token', os.getenv('AMPLIFY_TOKEN'))
            passed_system_prompt = kwargs.get('system_prompt', None)
            api_key = kwargs.get('api_key', None)
            model = kwargs.get('model', os.getenv('AMPLIFY_DEFAULT_MODEL', None))

            if not chat_url:
                raise ValueError("You must provide the URL of the Ampllify chat API to @prompt. Please set the "
                                 "environment variable 'AMPLIFY_CHAT_URL'.")

            if not api_key and not access_token:
                raise ValueError("You must provide an access token or API key to @prompt. Please set one of the "
                                 "environment variables 'AMPLIFY_TOKEN' or 'AMPLIFY_API_KEY'.")

            if not model:
                raise ValueError("You must provide a model to the function as a keyword arg or set the environment "
                                 "variable 'AMPLIFY_DEFAULT_MODEL'.")

            # Extract the first argument from bound arguments, which should be the input model instance
            input_model_instance = next(iter(bound_args.arguments.values()))

            # Ensure input model instance is a BaseModel
            if not isinstance(input_model_instance, BaseModel):
                raise TypeError("First argument must be a Pydantic BaseModel instance.")

            input_data = input_model_instance.dict()

            # Get function annotations and extract the output model from the return type annotation
            annotations = get_type_hints(func)
            output_model = annotations.get('return')

            if not output_model or not issubclass(output_model, BaseModel):
                raise ValueError("You must specify a Pydantic BaseModel as the return type in the function annotations.")

            result = None
            max_retries = 3

            while result is None and max_retries > 0:
                try:
                    max_retries -= 1
                    # Call the 'chat_llm' function (assuming chat_llm is defined elsewhere)
                    result = chat_llm(
                        docstring=func.__doc__,
                        system_prompt=passed_system_prompt or system_prompt,
                        inputs=input_data,
                        input_instance=input_model_instance,
                        output_model=output_model,
                        chat_url=chat_url,
                        access_token=access_token or api_key,
                        params=kwargs
                    )
                except Exception as e:
                    print(f"Error: {e}")
                    print("Retrying...")
                    continue


            return result
        return wrapper
    return decorator