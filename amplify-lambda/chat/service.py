from llm.chat import chat
import os
from common.validate import validate

@validate('read')
def chat_endpoint(event, context, current_user, name, data):
    payload = data['data']
    chat_url = os.environ['CHAT_ENDPOINT']
    access_token = current_user

    response, metadata = chat(chat_url, access_token, payload)
    return response