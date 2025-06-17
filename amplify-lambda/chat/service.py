import requests
from llm.chat import chat
import os
from common.validate import validated
from common.ops import op
import json
import os
from botocore.exceptions import ClientError
import boto3
from decimal import Decimal

@op(
    path="/chat",
    name="chatWithAmplify",
    method="POST",
    tags=["apiDocumentation"],
    description="""Interact with Amplify via real-time streaming chat capabilities, utilizing advanced AI models. 
    Example request: 
     {
    "data":{
        "temperature": 0.7,
        "max_tokens": 4000,
        "dataSources": ["yourEmail@vanderbilt.edu/2014-qwertyuio.json"],
        "messages": [
            {
            "role": "user",
            "content": "What is the capital of France?"
            }
        ],
        "options": {
            "ragOnly": false,
            "skipRag": true,
            "model": {"id": "gpt-4o"},
            "assistantId": "astp/abcdefghijk",
            "prompt": "What is the capital of France?"
        }
    }
}""",
    params={
        "model": "String representing the model ID. User can request a list of the models by calling the /available_models endpoint",
        "temperature": "Float value controlling the randomness of responses. Example: 0.7 for balanced outputs.",
        "max_tokens": "Integer representing the maximum number of tokens the model can generate in the response. Typically never over 2048. The user can confirm the max tokens for each model by calling the /available_models endpoint",
        "dataSources": "Array of strings representing input file ids, primarily used for retrieval-augmented generation (RAG). The user can make a call to the /files/query endpoint to get the id for their file. In the case of uploading a new data source through the /files/upload endpoint, the user can use the returned key as the id.",
        "messages": "Array of objects representing the conversation history. Each object includes 'role' (system/assistant/user) and 'content' (the message text). Example: [{'role': 'user', 'content': 'What is the capital of France?'}].",
        "options": {
            "ragOnly": "Boolean indicating whether only retrieval-augmented responses should be used. Example: false.",
            "skipRag": "Boolean indicating whether to skip retrieval-augmented generation. Example: true.",
            "assistantId": "String prefixed with 'astp' to identify the assistant. Example: 'astp/abcdefghijk'.",
            "model": "Object containing model-specific configurations, including 'id'. Example: {'id': 'gpt-4o'}. Must match the model id under the model attribute",
            "prompt": "String representing a system prompt for the model."
        }
    }
)
@validated(op = 'chat')
def chat_endpoint(event, context, current_user, name, data):
    access_token = data['access_token']
    access = data['allowed_access']
    if ('chat' not in access and 'full_access' not in access):
        return {'success': False, 'message': 'API key does not have access to chat functionality'}
    try:
        
        chat_endpoint = get_chat_endpoint()
        if (not chat_endpoint):
            return {"success": False, "message": "We are unable to make the request. Error: No chat endpoint found."}

        payload = data['data']
        assistant_id = payload["options"].get("assistantId")
        if (assistant_id):
            verify_assistant_id = validate_assistant_id(assistant_id, access_token)
            if (not verify_assistant_id["success"]):
                return {"success": False, "message": "Invalid assistant id"}
            
        payload["dataSources"] = get_data_source_details(payload["dataSources"])
        payload_options = payload["options"]
        payload["model"] = payload_options["model"]["id"]
        messages = payload["messages"]

        SYSTEM_ROLE = "system"
        if (messages[0]["role"] != SYSTEM_ROLE):
            print("Adding system prompt message")
            user_prompt = payload_options.get("prompt", "No Prompt Provided")
            payload["messages"] = [{"role": SYSTEM_ROLE, "content": user_prompt}] + messages

        response, metadata = chat(chat_endpoint, access_token, payload)
        return {"success": True, "message": "Chat endpoint response retrieved", "data": response}
    except Exception as e:
        return {"success": False, "message": {f"Chat service error: {e}"}}
    

def get_chat_endpoint():
    secret_name = os.environ['APP_ARN_NAME']
    region_name = os.environ.get('AWS_REGION', 'us-east-1')
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    try:
        print("Retrieving Chat Endpoint")
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
        secret_string = get_secret_value_response['SecretString']
        secret_dict = json.loads(secret_string)
        if ("CHAT_ENDPOINT" in secret_dict):
            return secret_dict["CHAT_ENDPOINT"]
        print("Chat Endpoint Not Found")
    except ClientError as e:
        print(f"Error getting secret: {e}")

    return None



def convert_decimal(obj):
    """Convert Decimal objects to Python native types (float or int)"""
    if isinstance(obj, Decimal):
        return float(obj) if obj % 1 != 0 else int(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal(item) for item in obj]
    return obj


def get_data_source_details(data_sources):
    if (len(data_sources) == 0): return []
    
    table_name = os.environ['FILES_DYNAMO_TABLE']  # Get the table name from the environment variable
    dynamodb = boto3.resource('dynamodb')
    data_source_ids = []

    for data_source in data_sources:
        id = None
        if isinstance(data_source, str):
            id = data_source
        elif isinstance(data_source, dict) and "id" in data_source:
            id = data_source["id"]

        if id:
            if id.startswith("s3://"):
                id = id.split("s3://")[1]
            data_source_ids.append(id)

    # Properly format batch_get_item request
    response = dynamodb.batch_get_item(
        RequestItems={
            table_name: {
                'Keys': [{'id': collection_id} for collection_id in data_source_ids]
            }
        }
    )

    # Format the response items as required
    formatted_sources = []
    found_ids = set()
    if 'Responses' in response and table_name in response['Responses']:
        items = response['Responses'][table_name]
        for item in items:
            found_ids.add(item.get("id", ""))
            # Create metadata object
            metadata = {
                "createdAt": item.get("createdAt", ""),
                "tags": item.get("tags", []),
                "totalTokens": item.get("totalTokens", 0)
            }
            id = item.get("id", "")
            
            # Create formatted object
            formatted_item = {
                "id": "s3://" + id,
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "data": item.get("data", ""),
                "metadata": metadata,
                "key": id 
            }
            formatted_sources.append(formatted_item)
    
    # Optional: track missing items
    missing_ids = set(data_source_ids) - found_ids
    if missing_ids:
        print(f"Warning: The following requested IDs were not found: {missing_ids}")
    
    # Convert any Decimal objects to regular Python types
    formatted_sources = convert_decimal(formatted_sources)
    
    return formatted_sources


def validate_assistant_id(assistant_id, access_token):
    print("Initiate call to validate assistant id: ", assistant_id)
    endpoint = os.environ['API_BASE_URL'] + '/assistant/validate/assistant_id'


    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            data=json.dumps({"data": {"assistantId": assistant_id} })
        )
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200:
            print("Error validating assistant id: ", response.content)
            return {"success": False}
        return response_content

    except Exception as e:
        print(f"Error validating assistant id: {e}")
        return {"success": False}
