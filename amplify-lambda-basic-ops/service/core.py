import os
import uuid

from common.validate import validated
from llm.chat import chat


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
        access_token = data['accessToken']

        data = data['data']

        datasource = data.get('dataSource', None)
        datasource_id = data.get('id', datasource.get('id', None) if datasource else None)

        print(f"Datasource ID: {datasource_id}")
        print(f"Datasource: {datasource}")

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
                'dataSource': [datasource['id'] if datasource else 'None'],
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to query the datasource"
        }


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

