import re
from operator import itemgetter
from typing import List, Dict, TypedDict, Any
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from langchain.agents.format_scratchpad import format_to_openai_functions
from langchain.agents.output_parsers import OpenAIFunctionsAgentOutputParser
from langchain_community.tools.convert_to_openai import format_tool_to_openai_function
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.retrievers import BaseRetriever
from fastapi.security import OAuth2PasswordBearer
from langchain_core.tools import Tool, tool
from langchain_openai import OpenAIEmbeddings
import langserve
from langserve import APIHandler, add_routes
from typing_extensions import Annotated

from common.auth import get_claims
from common.llm import get_chat_llm
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough, ConfigurableField, chain
from app.datasource.datasources import UserDataSources
from langchain.globals import set_debug
from langchain.callbacks import get_openai_callback
from datasource.local_db import load, describe_database_schema, execute_query_and_get_result_str, initialize_connection
from langchain.agents import initialize_agent, AgentType, AgentExecutor


def yes_no_chain(model, prompt, input):
    yes_no = (
            prompt
            | model
            | StrOutputParser()
            | RunnableLamlambda x: get_code_block(x, "answer");
    )

def get_code_block(string, code_type):
    # Adjust the regex pattern to use the `code_type` parameter
    pattern = rf"```{code_type}[\s\S]*?```"

    # Find the first occurrence of the specified code block type
    match = re.search(pattern, string, re.IGNORECASE | re.DOTALL)

    if match:
        # Extract the contents of the code block
        # Calculate the length of the opening tag to remove it correctly
        opening_tag_len = len(f"```{code_type}")
        code = match.group(0)[opening_tag_len:-3].strip()
        return code
    else:
        return None


system_dar_prompt = ("system", """"
    You are an expert assistant helping the Department of Alumni Relations (DAR) think of
    strategies to engage alumni donors and help decide which prospects different alumni
    development officers should reach out to. In order to do this, you design strategies
    and then turn these strategies into SQL queries against their database. You are restricted
    to strategies that can be implemented with their database schema and data using standard
    SQL. Whatever the user asks for, turn it into a strategy/plan and then create a SQL query
    for it. I will execute the query and show you the results. You can either return the results
    to the user or decide to modify your query and try again. I will always execute the queries and
    show the results to you. When you are happy with the query results, you can format them and
    return them to the user. Any SQL you produce needs to be in a ```sql code block.
    """)

task_prompt = ("human",""""Help me with this problem:"
--------------
{task}
--------------

Respond in this format:
```sql
<INSERT SQL>
```
""")

combined_prompt = ChatPromptTemplate.from_messages(
    [
        system_dar_prompt,
        task_prompt,
    ]
)

openai_model = get_chat_llm("gpt-35-turbo")

configurable_model = openai_model.configurable_alternatives(
    ConfigurableField(id="model"),
    default_key="gpt_35_turbo",
    gpt_4_turbo=get_chat_llm("gpt-4-turbo")
    # anthropic=anthropic,
)

sql_chain = (
        {"task": itemgetter("task")} |
        combined_prompt
        | openai_model
        | StrOutputParser()
        | parse_sql_code
        | execute_sql
)

async def per_req_config_modifier(config: Dict, request: Request) -> Dict:
    """Modify the config for each request."""
    user = await get_current_user(request)
    config["configurable"]["user_id"] = user
    return config


create_sql_chain = (
        {"task": itemgetter("task")} |
        combined_prompt
        | openai_model
        | StrOutputParser()
        | parse_sql_code
)

@tool
def create_sql(task: str) -> str:
    """
    Create SQL from a task.
    :param task: the description of the task in natural language
    :return: a SQL query that may perform the task
    """
    return create_sql_chain.invoke({"task": task})

@tool
def describe_db_schema() -> str:
    """
    Describe the database schema.
    """
    return describe_database_schema()


@tool
def execute_query(sql: str) -> str:
    """
    Execute a SQL query and return the results as a CSV string.
    :param sql:
    :return: results as a string
    """
    result = execute_query_and_get_result_str(sql)
    return result


# when giving tools to LLM, we must pass as list of tools
tools = [execute_query, describe_db_schema, create_sql]

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "You are a helpful assistant."),
        ("user", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ]
)


llm_with_tools = openai_model.bind(
    functions=[format_tool_to_openai_function(t) for t in tools])

agent = (
        {
            "input": lambda x: x["input"],
            "agent_scratchpad": lambda x: format_to_openai_functions(
                x["intermediate_steps"]
            ),
        }
        | prompt
        | llm_with_tools
        | OpenAIFunctionsAgentOutputParser()
)

agent_executor = AgentExecutor(agent=agent, tools=tools)

app = FastAPI(
    title="LangChain Server",
    version="1.0",
    description="Spin up a simple api server using Langchain's Runnable interfaces",
)


# We need to add these input/output schemas because the current AgentExecutor
# is lacking in schemas.
class AgentInput(TypedDict):
    input: str


class AgentOutput(TypedDict):
    output: Any


# Adds routes to the app for using the chain under:
# /invoke
# /batch
# /stream
#add_routes(app, agent_executor.with_types(input_type=Input, output_type=Output))

@chain
def execute_dar_agent(input: AgentInput):
    result = agent_executor.invoke({"input": input['input']})
    return result['output']

add_routes(
    app,
    execute_dar_agent.with_types(input_type=AgentInput, output_type=AgentOutput),
    #per_req_config_modifier=per_req_config_modifier,
    path="/dar-agent"
)



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

















# api_handler = APIHandler(
#     retrieval_chain,
#     # Namespace for the runnable.
#     # Endpoints like batch / invoke should be under /my_runnable/invoke
#     # and /my_runnable/batch etc.
#     path="/my_runnable",
# )
#
# @app.post("/my_runnable/invoke")
# async def invoke_with_auth(
#         invoke_request: api_handler.InvokeRequest,
#         request: Request,
#         current_user: Annotated[str, Depends(get_current_user)],
# ) -> Response:
#     """Handle a request."""
#     # The API Handler validates the parts of the request
#     # that are used by the runnnable (e.g., input, config fields)
#     config = {"configurable": {"user_id": current_user}}
#     return await api_handler.invoke(request, server_config=config)

