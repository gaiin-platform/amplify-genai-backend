import os
import traceback
import uuid

from pydantic import BaseModel, Field, ValidationError

from common.ops import op, vop
from common.validate import validated
from llm.chat import chat, prompt
from work.session import create_session

@op(
    path="/llm/query",
    tags=["llm", "default"],
    name="llmQueryDatasource",
    description="Query a datasource using the LLM.",
    params={
        "id": "The ID of the datasource to use for the query.",
        "query": "The 'query' or 'task' to use for the query.",
    }
)
@validated(op="query")
def llm_prompt_datasource(event, context, current_user, name, data):
    try:

        """
        
        This is an endpoint to prompt the LLM that can be used by an Assistant.
        We use the custom datasource endpoint format so that it can double as a
        datasource!
        
        All requests will include the following params:
        id - The ID of the datasource to use for the query.
        dataSource - The full json for the datasource to use for the query.
        query - The "query" or "task" to use for the query. This is based on the setting below. 
        
        Additional parameters may be included depending on the following settings.
        
        You will need to add your custom data source to the registry dynamo db table.
        
        The default registry table is: {your-app-name}-dev-datasource-registry
        Example: vu-amplify-dev-datasource-registry
        
        Each data source can have the following attributes in the registry entry:
        
        requestMethod - The HTTP method to use for the request. Default is 'POST'.
        endpoint - The endpoint to use for the request (the url of this function).
        includeAccessToken - Whether to include the user's access token in the request. Default is False.
                             It will be in an 'accessToken' field in the request body.
        includeAccount - Whether to include the user's account in the request. Default is False. It will be 
                         in an 'account' field in the request body.
        additionalParams - Any additional parameters to include in the request body. Default is an empty dictionary.
                           Each key/value pair will be included in the request body.
    
        queryMode - The mode to use for the query data that is sent. 
                    The options are:
                      lastMessageContent: include the string content of the last message in the conversation
                      lastMessage: include the JSON for the last message in the conversation
                      allMessages: include the JSON for all messages in the conversation
                      none: do not include any message data. 
                    
        """

        # This must be configured in the registry entry as described above
        access_token = data['access_token']

        data = data['data']

        if isinstance(data.get('id'), dict):
            datasource = data.get('id')
        else:
            datasource = data.get('dataSource', None)
        datasource_id = data.get('id', datasource.get('id', None) if datasource else None)

        print(f"Datasource ID: {datasource_id}")
        print(f"Datasource: {datasource}")

        if not datasource:
            datasource = {"id": datasource_id}

        custom_instructions = data.get('customInstructions', """
        Please follow the user's instructions EXTREMELY CAREFULLY. 
        If they ask you for information you don't have, just state that you don't have that information. Never guess.
        Stop. Think step by step
        how to accomplish the task. If you are provided any information for reference, try to
        quote directly from it with relevant information in the format "<Insert Quotation>" [Page/Slide/ect. X].
        If a query is used to produce the information, you can state that the information was produced by a query
        and provide the query. 
        """)
        query = data['query']
        account = data.get('account', 'default')

        # If you specified additionalParams, you could also extract them here from data.
        # This is an example with a default value in case it isn't configured.
        model = data.get('model', os.getenv('DEFAULT_LLM_QUERY_MODEL'))

        default_options = {
            'account': 'default',
            'model': os.getenv('DEFAULT_LLM_QUERY_MODEL'),
            'limit': 25
        }

        options = data.get('options', default_options)
        options = {**default_options, **options}

        result, meta_events = prompt_llm(access_token, model, datasource, custom_instructions, query, rag_only=False)

        print(f"The result of the prompt was: {result}")

        # See the location parameter in the result for controlling what shows up
        # in the sources of the response in the Amplify GenAI UI.

        return {
            'success': True,
            'data': result,

            # If the data is too big to fit in the context-window of the prompt, this
            # will allow the amplify-lambda-js chat to split it into multiple parts
            # and send it as multiple prompts. Don't allow splitting if it will mess
            # up the semantics of the data.
            'canSplit': True,

            # The meta events from the LLM
            'metaEvents': meta_events,

            # The keys for location can be arbitrary and will be passed to the UI.
            # Useful things to put in here are page, row, paragraph, etc. or anything
            # that can help the user look up the original data that you returned...
            # like the SQL query you used to generate it if you searched a database,
            # name of the database, etc.
            'location': {
                'name': 'llm',
                'prompt': query,
                'dataSource': [datasource['id'] if datasource else datasource_id],
            }
        }

    except Exception as e:
        # Print stack trace
        print(traceback.format_exc())

        print(e)
        return {
            'success': False,
            'message': "Failed to query the datasource"
        }


@vop(
    path="/session/create",
    tags=["session", "default"],
    name="createSession",
    description="Create a new session for the current user.",
    params={
        "conversation_id": "Optional ID of the conversation this session belongs to.",
        "tags": "Optional list of tags for the session.",
        "metadata": "Optional dictionary of metadata for the session."
    }
)


def prompt_llm(access_token, model, datasource, custom_instructions, query, rag_only=False):

    # the datasource as a list or an empty list if it is None
    datasources = [datasource] if datasource else []

    print(f"Prompting LLM with query: {query}")
    print(f"Using {len(datasources)} datasources: {datasources}")

    payload = {
        "model": model,
        "temperature": 1,
        "max_tokens": 1000,
        "stream": True,
        "dataSources": datasources,
        "messages": [
            {
                "role": "user",
                "content":
                    f"""
                    {query}
                    """,
                "type": "prompt",
                "data": {},
                "id": str(uuid.uuid4())
            }
        ],
        "options": {
            "requestId": str(uuid.uuid4()),
            "model": {
                "id": model,
            },
            "prompt": f"{custom_instructions}",
            "ragOnly": rag_only,
        }
    }

    chat_endpoint = os.getenv('CHAT_ENDPOINT')
    if not chat_endpoint:
        raise ValueError("Environment variable 'CHAT_ENDPOINT' is not set.")

    response, meta_events = chat(chat_endpoint, access_token, payload)



    return response, meta_events


class QAInput(BaseModel):
    input: str = Field(description="The input to perform the quality assurance on.")
    qa_guidelines: str = Field(description="The guidelines for quality assurance. Ensure that each guideline is followed carefully")


class QAOutput(BaseModel):
    qa_checks_passed: bool = Field(description="The QA result of True|False.")
    qa_reason: str = Field(description="The reason for the QA result.")


@prompt(system_prompt="Follow the instructions very carefully.")
def qa(input: QAInput) -> QAOutput:
    """
    Follow the instructions very carefully and ensure that each guideline is followed.
    If each guidelines is met, then output qa_pass_or_fail=True, otherwise qa_pass_or_fail=False.
    """
    pass


@vop(
    path="/llm/qa_check",
    tags=["llm", "default"],
    name="qaCheck",
    description="Perform a quality assurance check on a given input.",
    params={
        "input": "The input to perform the quality assurance on.",
        "qa_guidelines": "The guidelines for quality assurance."
    }
)
@validated(op="qa_check")
def llm_qa_check(event, context, current_user, name, data):
    try:
        """

        """
        # This must be configured in the registry entry as described above
        access_token = data['access_token']
        data = data['data']

        try:
            # Step 2: Create an instance of the model using the dictionary
            input = QAInput(**data)
            output = qa(input=input, access_token=access_token, model=os.getenv('DEFAULT_LLM_QUERY_MODEL'))

            return {
                'success': True,
                'data': output.model_dump(),
            }

        except ValidationError as e:
            print(e)
            return {
                'success': False,
                'message': "Invalid parameters {e}"
            }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to execute the operation"
        }




# Example usage:
#
# result = qa(QAInput(
#     input="data: { 'id': '1234', 'name': 'John Doe' }"
#           "original_input: John Doe was walking by aisle 1234 when a box fell on him.",
#     qa_guidelines=("1. The data must include an id that is also a valid id in the original input. "
#                    "2. The name in data must correspond to the person's name in the original_input.")),
#     model="gpt-4o")
#
# if not result.qa_pass_or_fail:
#     print(f"QA failed: {result.qa_reason}")
# else:
#     print("QA passed")

# "data: { 'id': '1234', 'name': 'John Doe' }\noriginal_input: John Doe was walking by aisle 1234 when a box fell on him.",