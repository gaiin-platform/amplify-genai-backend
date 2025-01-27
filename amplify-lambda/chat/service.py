from llm.chat import chat
import os
from common.validate import validated
from common.ops import op
import json
import os
from botocore.exceptions import ClientError
import boto3

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
        "dataSources": [{"id": "s3://user@vanderbilt.edu/2014-qwertyuio","type": "application/pdf"}],
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
        "dataSources": "Array of objects representing input files or documents for retrieval-augmented generation (RAG). Each object must contain an 'id' and 'type'. Example: [{'id': 's3://example_file.pdf', 'type': 'application/pdf'}]. The user can make a call to the /files/query endpoint to get the id for their file.",
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
    access = data['allowed_access']
    if ('chat' not in access and 'full_access' not in access):
        return {'success': False, 'message': 'API key does not have access to chat functionality'}
    try:
        
        chat_endpoint = get_chat_endpoint()
        if (not chat_endpoint):
            return {"success": False, "message": "We are unable to make the request. Error: No chat endpoint found."}
        access_token = data['access_token']

        payload = data['data']
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
        return {"success": False, "message": {f"Error: {e}"}}
    

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

