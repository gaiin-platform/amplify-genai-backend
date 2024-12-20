from llm.chat import chat
import os
from common.validate import validated
from common.ops import op

@op(
    path="/chat",
    name="chatWithAmplify",
    method="POST",
    tags=["apiDocumentation"],
    description="""Interact with Amplify via real-time streaming chat capabilities, utilizing advanced AI models. 
    Example request: 
     {
    "data":{
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_tokens": 150,
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
        payload = data['data']
        # print(payload)
        chat_url = os.environ['CHAT_ENDPOINT']
        access_token = data['access_token']

        response, metadata = chat(chat_url, access_token, payload)
        return {"success": True, "message": "Chat completed successfully", "data": response}
    except Exception as e:
        return {"success": False, "message": {f"Error: {e}"}}