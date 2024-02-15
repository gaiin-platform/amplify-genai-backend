from common.validate import validated
from common.llm import get_chat_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
import logging

from openai import AzureOpenAI
import os
import json
from common.credentials import get_credentials, get_endpoint
from common.validate import validated


# Logging configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# model_name = os.environ["MODEL_NAME"]
# model_name = "gpt-35-turbo"
# endpoints_arn = os.environ["ENDPOINTS_ARN"]
# endpoint, api_key = get_endpoint(endpoints_arn, model_name)
# pg_password = get_credentials(rag_pg_password)

# client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version="2023-05-15")


# @validate will check the incoming JWT, determine the current user, and
# make sure that the incoming request body has the format {data:{task:"what to do"}}
@validated("execute_rename")
def execute_rename(event, context, current_user, name, data):
    """
    This is entry point to the lambda function.

    Rename a conversation by briefly summarizing the user's prompt.

    :param event: AWS lambda event
    :param context: AWS lambda context
    :param current_user: string name of the current user as obtained from JWT token
    :param name: name of the operation to be performed, execute_rename
    :param data: data passed by the client in the foramt {data:{...}}
    :return:
    """
    data = data["data"]
    model = data.get("model", None)
    user_prompt = data.get("task")
    return generate_chat_name(model, current_user, user_prompt)


def generate_chat_name(model, current_user, user_prompt):
    """
    Generates a brief name for a chat conversation using a language model.

    This function uses a specified language model (or a default model if none is provided) to generate a concise title for a chat conversation based on the user's prompt. The title is intended to summarize the conversation's theme or content in under 30 characters.

    :param model: The model identifier to use for generation. If None, uses a default model specified in the environment.
    :param current_user: The name of the current user, for context.
    :param user_prompt: The user's prompt describing the conversation.
    :return: A brief, generated chat name.
    """
    try:
        # Use LLM to generate SQL query based on user prompt and schema information
        # Check if model is not None or use the os.environ.get("DEFAULT_MODEL", "gpt-3.5-turbo")
        # model = model if model else os.environ.get("DEFAULT_MODEL", "gpt-35-turbo")
        # print(f"Using model: {model}")
        llm = get_chat_llm("gpt-35-turbo")

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "As an AI proficient in summarization, create a title for this conversation based on the following prompt. Ensure the title is under 30 characters.",
                ),
                ("user", user_prompt),
            ]
        )

        output_parser = StrOutputParser()

        # Chain the components together
        chain = prompt | llm | output_parser

        # Log the prompt
        # logging.info(f"Sending prompt to LLM:\n{formatted_prompt}")

        # Invoke the chain with an empty input since the prompt already contains all necessary information
        return chain.invoke({"input": ""})

    except Exception as e:
        logging.error(f"Error in generate_chat_name: {e}")
        raise
