import os
import uuid

from common.validate import validated
from llm.chat import chat


@validated(op="query")
def query_datasource(event, context, current_user, name, data):
    try:

        """
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
        datasource_id = data['id']
        datasource = data['dataSource']
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

        result = None

        if datasource_id == 'animal_facts':
            result = random_animal_facts(access_token, model, query)
        else:
            result = random_animal_data()

        print(f"The result of the crazy animal query was: {result}")

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

            # The keys for location can be arbitrary and will be passed to the UI.
            # Useful things to put in here are page, row, paragraph, etc. or anything
            # that can help the user look up the original data that you returned...
            # like the SQL query you used to generate it if you searched a database,
            # name of the database, etc.
            'location': {
                'name': 'hallucination',
                'dream': 'llm',
                'dreamer': model
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to query the datasource"
        }


def random_animal_data():
    return {
        "animals": [
            {
                "name": "Dog",
                "facts": [
                    "Dogs are loyal.",
                    "Dogs are friendly.",
                    "Dogs are cute."
                ]
            },
            {
                "name": "Cat",
                "facts": [
                    "Cats are independent.",
                    "Cats are curious.",
                    "Cats are agile."
                ]
            },
            {
                "name": "Elephant",
                "facts": [
                    "Elephants are intelligent.",
                    "Elephants are social.",
                    "Elephants are strong."
                ]
            },
            {
                "name": "Penguin",
                "facts": [
                    "Penguins are flightless birds.",
                    "Penguins are excellent swimmers.",
                    "Penguins live in cold climates."
                ]
            },
            {
                "name": "Kangaroo",
                "facts": [
                    "Kangaroos are marsupials.",
                    "Kangaroos hop to move around.",
                    "Kangaroos are native to Australia."
                ]
            },
            {
                "name": "Dolphin",
                "facts": [
                    "Dolphins are highly intelligent.",
                    "Dolphins are social animals.",
                    "Dolphins use echolocation to communicate."
                ]
            },
            {
                "name": "Giraffe",
                "facts": [
                    "Giraffes are the tallest mammals on Earth.",
                    "Giraffes have long necks.",
                    "Giraffes have a unique spotted pattern."
                ]
            },
            {
                "name": "Zebra",
                "facts": [
                    "Zebras have black and white stripes.",
                    "Zebras are herbivores.",
                    "Zebras live in groups called 'harems'."
                ]
            },
            {
                "name": "Lion",
                "facts": [
                    "Lions are the king of the jungle.",
                    "Lions are social animals.",
                    "Lions are carnivores."
                ]
            },
            {
                "name": "Tiger",
                "facts": [
                    "Tigers are the largest cat species.",
                    "Tigers are solitary animals.",
                    "Tigers have striped fur."
                ]
            }
        ]
    }


def random_animal_facts(access_token, model, query):

    payload = {
        "model": model,
        "temperature": 1,
        "max_tokens": 1000,
        "stream": True,
        "dataSources": [],
        "messages": [
            {
                "role": "user",
                "content":
                    f"""
                      Yo! We need to make up crazy animal facts no matter what to amuse the user.
                      The user said:
                      ----------
                      {query}
                      ----------
                      
                      Give me some crazy animal facts to make the user laugh related to what they asked.
                    """,
                "type": "prompt",
                "data": {},
                "id": "example-id-1234"
            }
        ],
        "options": {
            "requestId": str(uuid.uuid4()),
            "model": {
                "id": model,
            },
            "prompt": "Follow the user's instructions carefully. Respond using the exact format specified.",
            "ragOnly": True,
        }
    }

    chat_endpoint = os.getenv('CHAT_ENDPOINT')
    if not chat_endpoint:
        raise ValueError("Environment variable 'CHAT_ENDPOINT' is not set.")

    response, _ = chat(chat_endpoint, access_token, payload)
    return response

