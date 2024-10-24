from operator import itemgetter
from typing import List, Dict
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from fastapi.security import OAuth2PasswordBearer
from langchain_openai import OpenAIEmbeddings
import langserve
from langserve import APIHandler, add_routes
from typing_extensions import Annotated

from common.auth import get_claims
from common.llm import get_chat_llm
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, ConfigurableField
from app.datasource.datasources import UserDataSources
from langchain.globals import set_debug
from langchain.callbacks import get_openai_callback

set_debug(True)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI(
    title="Amplify Assistant Server",
    version="1.0",
    description="Amplify Chat Services",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.get("/")
async def redirect_root_to_docs():
    return RedirectResponse("/docs")


async def get_current_user(request: Request) -> str:
    token = await oauth2_scheme(request)
    claims = None

    try:
        claims = get_claims(token)
    except Exception as e:
        print(f"Failed to get claims from token: {str(e)}")

    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # remove vupingidp_ prefix
    username = claims['username'].split('_')[1]
    return username


retriever = UserDataSources(
    user_id=None,  # Placeholder ID that will be replaced by the per_req_config_modifier
).configurable_fields(
    # Attention: Make sure to override the user ID for each request in the
    # per_req_config_modifier. This should not be client configurable.
    user_id=ConfigurableField(
        id="user_id",
        name="User ID",
        description="The user ID to use for the retriever.",
    )
)


template = """Answer the question based only on the following context:
{chat_history}
Owner:{context} owns Account Type:{account}

Question: {question}
"""
prompt = ChatPromptTemplate.from_template(template)
openai_model = get_chat_llm("gpt-35-turbo")

configurable_model = openai_model.configurable_alternatives(
    ConfigurableField(id="model"),
    default_key="gpt_35_turbo",
    gpt_4_turbo=get_chat_llm("gpt-4-turbo")
    # anthropic=anthropic,
)


retrieval_chain = (
        {"context": retriever,
         "question": itemgetter("question"),
         "account": itemgetter("account")}
        | RunnablePassthrough.assign(
            chat_history=lambda x: "You previously stated my name was Bob"
        )
        | prompt
        | configurable_model
        | StrOutputParser()
)

async def per_req_config_modifier(config: Dict, request: Request) -> Dict:
    """Modify the config for each request."""
    user = await get_current_user(request)
    config["configurable"]["user_id"] = user
    return config


# Edit this to add the chain you want to add
add_routes(
    app,
    configurable_model,
    path="/openai"
)

add_routes(
    app,
    retrieval_chain,
    per_req_config_modifier=per_req_config_modifier,
    path="/retrieval"
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

