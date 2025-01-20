from llm.chat import chat_simple_messages


def generate_response(access_token, model, messages: []) -> str:
    response = chat_simple_messages(access_token, model, messages)
    return response

def create_llm(access_token, model):
    llm = lambda prompt: generate_response(access_token, model, prompt)
    return llm

