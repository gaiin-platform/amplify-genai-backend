import json
import os
import threading
import traceback
import uuid

import boto3
from pydantic import BaseModel, Field, ValidationError

from service.jobs import set_job_result, init_job_status, update_job_status
from flow.steps import parse_workflow
from llm.chat import prompt
from pycommon.llm.chat import chat
from pycommon.api.get_endpoint import get_endpoint, EndpointType

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_route_data, set_permissions_by_state
from service.routes import route_data

set_route_data(route_data)
set_permissions_by_state(permissions)


@api_tool(
    path="/llm/rag_query",
    tags=["llm", "default"],
    name="llmRagQueryDatasource",
    description="Search for information in a datasource using the LLM. This is a lightweight, less expensive search that works when targeted questions are sufficient. It doesn't guarantee that all relevant information is found.",
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The ID of the datasource to use for the query",
            },
            "query": {
                "type": "string",
                "description": "The 'query' or 'task' to use for the query",
            },
        },
        "required": ["id", "query"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {"type": "string", "description": "The LLM response content"},
            "canSplit": {
                "type": "boolean",
                "description": "Whether the data can be split into multiple parts",
            },
            "metaEvents": {
                "type": "array",
                "description": "Meta events from the LLM processing",
            },
            "location": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "prompt": {"type": "string"},
                    "dataSource": {"type": "array", "items": {"type": "string"}},
                },
                "description": "Location metadata for the response",
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
)
@validated(op="rag_query")
def llm_prompt_datasource_rag(event, context, current_user, name, data):
    data["ragOnly"] = True
    return llm_prompt_datasource(event, context, current_user, name, data)


@api_tool(
    path="/llm/query",
    tags=["llm", "default"],
    name="llmQueryDatasource",
    description="Query a datasource using the LLM. This is a much more extensive and robust, but expensive, search. It looks at the entire document and can be used when you need all information related to a topic",
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The ID of the datasource to use for the query",
            },
            "query": {
                "type": "string",
                "description": "The 'query' or 'task' to use for the query",
            },
        },
        "required": ["id", "query"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {"type": "string", "description": "The LLM response content"},
            "canSplit": {
                "type": "boolean",
                "description": "Whether the data can be split into multiple parts",
            },
            "metaEvents": {
                "type": "array",
                "description": "Meta events from the LLM processing",
            },
            "location": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "prompt": {"type": "string"},
                    "dataSource": {"type": "array", "items": {"type": "string"}},
                },
                "description": "Location metadata for the response",
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
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
        access_token = data["access_token"]

        data = data["data"]

        if isinstance(data.get("id"), dict):
            datasource = data.get("id")
        else:
            datasource = data.get("dataSource", None)
        datasource_id = data.get(
            "id", datasource.get("id", None) if datasource else None
        )

        rag_only = data.get("ragOnly", False)

        print(f"Datasource ID: {datasource_id}")
        print(f"Datasource: {datasource}")

        if not datasource:
            datasource = {"id": datasource_id}

        custom_instructions = data.get(
            "customInstructions",
            """
        Please follow the user's instructions EXTREMELY CAREFULLY. 
        If they ask you for information you don't have, just state that you don't have that information. Never guess.
        Stop. Think step by step
        how to accomplish the task. If you are provided any information for reference, try to
        quote directly from it with relevant information in the format "<Insert Quotation>" [Page/Slide/ect. X].
        If a query is used to produce the information, you can state that the information was produced by a query
        and provide the query. 
        """,
        )
        query = data["query"]
        account = data.get("account", "default")

        # If you specified additionalParams, you could also extract them here from data.
        # This is an example with a default value in case it isn't configured.
        model = data.get("model", os.getenv("DEFAULT_LLM_QUERY_MODEL"))

        default_options = {
            "account": "default",
            "model": os.getenv("DEFAULT_LLM_QUERY_MODEL"),
            "limit": 25,
        }

        options = data.get("options", default_options)
        options = {**default_options, **options}

        result, meta_events = prompt_llm(
            access_token, model, datasource, custom_instructions, query, rag_only
        )

        print(f"The result of the prompt was: {result}")

        # See the location parameter in the result for controlling what shows up
        # in the sources of the response in the Amplify GenAI UI.

        return {
            "success": True,
            "data": result,
            # If the data is too big to fit in the context-window of the prompt, this
            # will allow the amplify-lambda-js chat to split it into multiple parts
            # and send it as multiple prompts. Don't allow splitting if it will mess
            # up the semantics of the data.
            "canSplit": True,
            # The meta events from the LLM
            "metaEvents": meta_events,
            # The keys for location can be arbitrary and will be passed to the UI.
            # Useful things to put in here are page, row, paragraph, etc. or anything
            # that can help the user look up the original data that you returned...
            # like the SQL query you used to generate it if you searched a database,
            # name of the database, etc.
            "location": {
                "name": "llm",
                "prompt": query,
                "dataSource": [datasource["id"] if datasource else datasource_id],
            },
        }

    except Exception as e:
        # Print stack trace
        print(traceback.format_exc())

        print(e)
        return {"success": False, "message": "Failed to query the datasource"}


def prompt_llm(
    access_token, model, datasource, custom_instructions, query, rag_only=False
):

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
                "content": f"""
                    {query}
                    """,
                "type": "prompt",
                "data": {},
                "id": str(uuid.uuid4()),
            }
        ],
        "options": {
            "requestId": str(uuid.uuid4()),
            "model": {
                "id": model,
            },
            "prompt": f"{custom_instructions}",
            "ragOnly": rag_only,
        },
    }

    chat_endpoint = get_endpoint(EndpointType.CHAT_ENDPOINT)
    if not chat_endpoint:
        raise ValueError("Couldnt retrieve 'CHAT_ENDPOINT' from secrets manager.")

    response, meta_events = chat(chat_endpoint, access_token, payload)

    return response, meta_events


class QAInput(BaseModel):
    input: str = Field(description="The input to perform the quality assurance on.")
    qa_guidelines: str = Field(
        description="The guidelines for quality assurance. Ensure that each guideline is followed carefully"
    )


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


@api_tool(
    path="/llm/qa_check",
    tags=["llm", "default"],
    name="qaCheck",
    description="Perform a quality assurance check on a given input.",
    parameters={
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "The input to perform the quality assurance on",
            },
            "qa_guidelines": {
                "type": "string",
                "description": "The guidelines for quality assurance",
            },
        },
        "required": ["input", "qa_guidelines"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "qa_checks_passed": {
                        "type": "boolean",
                        "description": "Whether the QA checks passed",
                    },
                    "qa_reason": {
                        "type": "string",
                        "description": "The reason for the QA result",
                    },
                },
                "description": "QA check results",
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
)
@validated(op="qa_check")
def llm_qa_check(event, context, current_user, name, data):
    try:
        """ """
        # This must be configured in the registry entry as described above
        access_token = data["access_token"]
        data = data["data"]

        try:
            # Step 2: Create an instance of the model using the dictionary
            input = QAInput(**data)
            output = qa(
                input=input,
                access_token=access_token,
                model=os.getenv("DEFAULT_LLM_QUERY_MODEL"),
            )

            return {
                "success": True,
                "data": output.model_dump(),
            }

        except ValidationError as e:
            print(e)
            return {"success": False, "message": "Invalid parameters {e}"}

    except Exception as e:
        print(e)
        return {"success": False, "message": "Failed to execute the operation"}


@api_tool(
    path="/llm/workflow-start",
    tags=["default"],
    name="startWorkflow",
    description="Starts asynchronous execution of the specified workflow.",
    parameters={
        "type": "object",
        "properties": {
            "template": {
                "type": "object",
                "description": "The workflow template as JSON in the Amplify Workflow Template Language",
            },
            "context": {
                "type": "object",
                "description": "An optional set of context parameters as a JSON dictionary",
            },
        },
        "required": ["template"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The unique identifier for the started workflow job",
                    }
                },
                "description": "Workflow job information",
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
)
@validated(op="llm_workflow_async")
def llm_workflow_async(event, context, current_user, name, data):
    try:
        """ """
        # This must be configured in the registry entry as described above
        access_token = data["access_token"]
        data = data["data"]
        template_doc = data["template"]

        job_id = start_workflow_lambda(current_user, access_token, data)

        return {"success": True, "data": {"job_id": job_id}}

    except Exception as e:
        print(f"Error: {str(e)}")
        return {"success": False, "message": "Failed to execute the operation"}


@validated(op="llm_workflow")
def llm_workflow(event, context, current_user, name, data):
    try:
        """ """
        # This must be configured in the registry entry as described above
        access_token = data["access_token"]
        data = data["data"]
        template_doc = data["template"]

        try:

            try:
                trace_lock = threading.Lock()
                trace = []

                def progress_callback(percent):
                    print(f"--- Progress: {percent}%")

                def recording_tracer(id, tag, data, log_file="trace_log.yaml"):
                    try:
                        logdata = data
                        if isinstance(data, dict):
                            logdata = next(
                                (
                                    data[key].keys()
                                    for key in ["result", "context"]
                                    if key in data
                                ),
                                data,
                            )
                        elif isinstance(data, list):
                            logdata = f"list[{len(data)}]"
                        print(f"--- Step {id}: {tag} - {logdata}")
                        with trace_lock:
                            trace.append({"id": id, "tag": tag, "data": data})
                    except Exception as e:
                        print(f"--- Error recording trace: {str(e)}")

                steps = parse_workflow(template_doc)

                print(f"--- Executing workflow: {steps} ")

                result = steps.exec(
                    data.get("context", {}),
                    {
                        "access_token": access_token,
                        "model": "gpt-4o",
                        "output_mode": "yaml",
                        "tracer": recording_tracer,
                        "progress_callback": progress_callback,
                    },
                )

                return result

            except Exception as e:
                # print a detailed stack trace
                print(f"--- ERROR {str(e)}")
                return {"message": f"Error executing steps: {str(e)}"}

        except ValidationError as e:
            print(e)
            return {"success": False, "message": "Invalid parameters {e}"}

    except Exception as e:
        print(e)
        return {"success": False, "message": "Failed to execute the operation"}


# The Lambda function (llm_workflow_lambda.py):
def llm_workflow_lambda_handler(event, context):

    # Extract parameters from the event
    current_user = event.get("current_user")
    job_id = event.get("job_id")
    access_token = event.get("access_token")

    print(f"Starting workflow for user {current_user} with job_id {job_id}")

    try:
        data = event.get("payload")
        # Thread-safe trace recording
        trace_lock = threading.Lock()
        trace = []

        def progress_callback(percent):
            print(f"--- Progress: {percent}%")

        def recording_tracer(id, tag, data, log_file="trace_log.yaml"):
            try:
                logdata = data
                logdatajson = json.dumps(data)

                update_job_status(
                    current_user,
                    job_id,
                    {
                        "status": f"Step {id}: {tag}",
                        "data": data if len(logdatajson) <= 35000 else {},
                        "retryIn": 2000,
                    },
                )

                if isinstance(data, dict):
                    logdata = next(
                        (
                            data[key].keys()
                            for key in ["result", "context"]
                            if key in data
                        ),
                        data,
                    )
                elif isinstance(data, list):
                    logdata = f"list[{len(data)}]"
                print(f"--- Step {id}: {tag} - {logdata}")
                with trace_lock:
                    trace.append({"id": id, "tag": tag, "data": data})
            except Exception as e:
                print(f"--- Error recording trace: {str(e)}")

        workflow_data = data["data"]

        print(f"Workflow data: {workflow_data}")

        template_doc = workflow_data["template"]

        steps = parse_workflow(template_doc)
        print(f"--- Executing workflow: {steps} ")

        result = steps.exec(
            workflow_data.get("context", {}),
            {
                "access_token": access_token,
                "model": "gpt-4o",
                "output_mode": "yaml",
                "tracer": recording_tracer,
                "progress_callback": progress_callback,
            },
        )

        print(f"--- Workflow result: {result}")
        set_job_result(current_user, job_id, result, store_in_s3=True)
        print(f"--- Job result stored successfully")

    except Exception as e:
        print(f"Error: {str(e)}")
        set_job_result(current_user, job_id, {"success": False, "message": str(e)})


def start_workflow_lambda(current_user, access_token, data):
    lambda_client = boto3.client("lambda")

    print(f"Starting workflow for user {current_user}")
    job_id = init_job_status(current_user, "Workflow started and running...")

    print("Initialized job status")

    # Prepare the payload
    payload = {
        "name": "workflow_name",
        "current_user": current_user,
        "access_token": access_token,
        "job_id": job_id,
        "payload": {
            "data": data,
        },
    }

    try:
        print(f"Invoking workflow lambda with payload: {payload}")

        lambda_name = os.getenv("WORKFLOW_LAMBDA_NAME")
        print(f"Workflow lambda name: {lambda_name}")

        lambda_client.invoke(
            FunctionName=lambda_name,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )

        print(f"Workflow lambda invoked successfully")

        return job_id

    except Exception as e:
        print(f"Error invoking workflow lambda: {str(e)}")
        raise
