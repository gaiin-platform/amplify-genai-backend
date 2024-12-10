import os
import uuid
from typing import List

import yaml

from common.ops import op
from common.validate import validated
from llm.chat import chat, prompt
from pydantic import BaseModel, Field



@op(
    path="/optimizer/prompt",
    name="generateOptimizedPrompt",
    tags=["prompts"],
    description="Generate an optimized prompt for a task.",
    params={
        "prompt": "The task to generate a prompt template for.",
        "maxPlaceholders": "The maximum number of placeholders to use in the prompt template."
    }
)
@validated(op="optimize")
def optimize(event, context, current_user, name, data):
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

        access_token = data['access_token']

        data = data['data']
        datasource = data.get('dataSource', None)
        query = data.get('prompt', data.get('query', None))
        account = data.get('account', 'default')
        max_placeholders = data.get('maxPlaceholders', 3)

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

        chat_url = os.getenv('CHAT_ENDPOINT')
        model = os.getenv('DEFAULT_LLM_QUERY_MODEL')

        idea = PromptInput(task=query, max_placeholders=max_placeholders)
        result = prompt_generator(
            task=idea,
            model=model,
            access_token=access_token,
            chat_url=chat_url)

        # See the location parameter in the result for controlling what shows up
        # in the sources of the response in the Amplify GenAI UI.

        return {
            'success': True,
            'data': result,

            # If the data is too big to fit in the context-window of the prompt, this
            # will allow the amplify-lambda-js chat to split it into multiple parts
            # and send it as multiple prompts. Don't allow splitting if it will mess
            # up the semantics of the data.
            'canSplit': False,

            # The keys for location can be arbitrary and will be passed to the UI.
            # Useful things to put in here are page, row, paragraph, etc. or anything
            # that can help the user look up the original data that you returned...
            # like the SQL query you used to generate it if you searched a database,
            # name of the database, etc.
            'location': {
                'name': 'prompt_generator',
            }
        }

    except Exception as e:
        print("Error: ", e)
        return {
            'success': False,
            'message': "Failed to generate an optimized prompt."
        }


class PromptInput(BaseModel):
    task: str = Field(description="The task to generate a prompt template for.")
    max_placeholders: int = Field(description="The maximum number of placeholders to use in the prompt template.")


class PromptTemplateOutput(BaseModel):
    prompt_template: str = Field(description="The template for a useful prompt.")


@prompt(system_prompt="Follow the instructions very carefully.")
def prompt_generator(task: PromptInput) -> PromptTemplateOutput:
    """
    These are the bounds that we are going to place on how we use LLM in the workplace:
    We are going to use the following framework in exploring how to use Generative AI to aid people:
    1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
    multiple potential interpretations of data, identifying assumptions in their decisions and helping them
    evaluate the validity of those assumptions, often by challenging them.
    2. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of
    different and diverse options for any task.
    3. Simultaneously applying multiple structured approaches to representing and solving problems.
    4. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
    drafts that are good starting points.
    5. Aiding in summarization, drafting of plans, identification of
    supporting quotations or evidence, identification of assumptions, in 3-5 pages of text. Provide one
    approach to using ChatGPT to perform the following and one specific prompt that would be used for this.
    6. Prompts that walk the user through a step-by-step diagnosis process by instructing the LLM to ask
    the user questions one at a time,
    waiting for the answer, and then asking the next question based on the answer to the previous question until
    the diagnosis is complete, enough information is collected for the LLM to perform a task, the user has learned something
    an analysis is complete, etc. These prompts should include a specific instruction to tell the LLM to ask the user a question or
    do something one at a time, wait for the answer, and then have the LLM ask the next question, etc. The LAST statement in these
    prompts must be exactly "Ask the first question" or "Tell me the first step". The LLM can also ask the user to run
    a command, run code that it writes in python, etc. to collect information or perform a task in a computer
    system (e.g., run a query on a database, run a bash command to collect diagnostic info, etc.).
    7. Extracting structured information from unstructured text by having the LLM extract and reformat
    the information into a new structured format.

    First, think about the inputs that the user would need to provide to the prompt to make sure it has
    the relevant outside information as context.

    Make sure and include placeholders like {{INSERT TEXT}} (insert actual newlines) if the prompt relies on
    outside information, etc. If the prompt relies on a lot of information (e.g., more than a sentence or two),
    separate it like this:
    ----------------
    {{2-3 Word Description of the Information Needed}}
    ----------------

    Examples:
    ----------------
    {{Name of the Company}}
    {{Description of the Company}}
    {{Industry of the Company}}
    ----------------

    If the information that is needed is a file, you can suffix the placeholder with the file type like this:
    {{Some File You Need:file}}

    Examples:
    ----------------
    {{Company Financials:file}}
    {{Company Description:file}}
    ----------------

    The only allowed suffix is file. Otherwise, it should have no suffix.

    If there is no additional information that is needed, you can just create the prompt template with no
    placeholders.

    Be thoughtful and detailed in creating a really useful prompt that can be reused and are very detailed.
    If there is a specific domain that the prompt is for, make sure to include a detailed "Act as ..." with
    a detailed description of the role that the LLM is supposed to take on.

    Unless told otherwise, you may ask for AT MOST 3-4 pieces of information with placeholders.
    Everything else must be inferred by the LLM.

    If you are creating a specific format in markdown for the LLM to fill in, you can leave placeholders for
    the LLM to fill in with the format <Insert XYZ>. For example, you might have a template like:
    ## <Insert First Quiz Question>
    | Question | Answer |
    |----------|--------|
    | <Insert Question 1> | <Insert Answer 1> |
    | <Insert Question 2> | <Insert Answer 2> |
    ----------------
    You could also have a template like:
    <Facts>
    {{Numbered List of Facts}}
    </Facts>
    ## Summary
    <Insert Summary of Facts with markdown Footnotes Supporting Each Sentence>
    ## Footnotes
    <Insert Footnotes with each fact from the Facts>

    Create an extremely useful prompt for an LLM based on the specified task.
    """
    pass
