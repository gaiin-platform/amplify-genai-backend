# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from . import code_interpreter_api as assistants
import random
import string
import re
from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, S3Operation
)
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value])

from pycommon.logger import getLogger
logger = getLogger("code_interpreter")

@api_tool(
    path="/assistant/chat/codeinterpreter",
    name="chatWithCodeInterpreter",
    method="POST",
    tags=["apiDocumentation"],
    description="""Initiate a conversation with the Code Interpreter. Each request can append new messages to the existing conversation using a unique assistant ID.
    Data source keys for files can be found by calling files/query
    Example request:
    {
      "data": {
          "codeInterpreterRecordId": "yourEmail@vanderbilt.edu/ast/43985037429849290398",
          "messages": [
              { "role": "user",
                "content" : "Can you tell me something about the data analytics and what you are able to do?",
                "dataSourceIds" : ["yourEmail@vanderbilt.edu/2024-05-08/0f20f0447b.json"]
              }
          ]
      }
  }

    Example response:
    {
      "success": true,
      "message": "Chat completed successfully",
      "data": {
        "sessionId": "yourEmail@vanderbilt.edu/ast/43985037429849290398",
        "role": "assistant",
        "textContent": "I've generated the pie chart as a PNG file.\\n",
        "content": [
          {
            "type": "image/png",
            "values": {
              "file_key": "yourEmail@vanderbilt.edu/abc123-FN-generated_image.png",
              "presigned_url": "https://...",
              "file_size": 149878
            }
          }
        ]
      }
    }

    """,
    parameters={
        "type": "object",
        "properties": {
            "codeInterpreterRecordId": {
                "type": "string",
                "description": "Unique identifier of the Code Interpreter record. Example: 'yourEmail@vanderbilt.edu/ast/43985037429849290398'.",
            },
            "messages": {
                "type": "array",
                "description": "New conversation messages. Each object includes 'role' (user/system/assistant), 'content' as a string, and 'dataSourceIds' a list of strings.",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": ["user", "system", "assistant"],
                        },
                        "content": {"type": "string"},
                        "dataSourceIds": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["role", "content"],
                },
            },
        },
        "required": ["codeInterpreterRecordId", "messages"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the chat operation was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "sessionId": {
                        "type": "string",
                        "description": "The assistant/session ID for the conversation",
                    },
                    "role": {
                        "type": "string",
                        "description": "The role of the response (typically 'assistant')",
                    },
                    "textContent": {
                        "type": "string",
                        "description": "The text response from the assistant",
                    },
                    "content": {
                        "type": "array",
                        "description": "Array of content objects (files, images, etc.)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "Content type (e.g., 'image/png')",
                                },
                                "values": {
                                    "type": "object",
                                    "properties": {
                                        "file_key": {
                                            "type": "string",
                                            "description": "Unique file key for the generated content",
                                        },
                                        "presigned_url": {
                                            "type": "string",
                                            "description": "Pre-signed URL for downloading the file",
                                        },
                                        "file_size": {
                                            "type": "integer",
                                            "description": "Size of the file in bytes",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "required": ["success", "message"],
    },
)
@required_env_vars({
    "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_IMAGE_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT, S3Operation.GET_OBJECT],
})
@validated(op="chat")
def chat_with_code_interpreter(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.ASSISTANTS.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality",
        }

    logger.debug("Chat_with_code_interpreter validated")
    record_id = data["data"]["codeInterpreterRecordId"]
    messages = data["data"]["messages"]

    api_accessed = data["api_accessed"]
    request_id = generate_req_id() if api_accessed else data["data"]["requestId"]

    return assistants.chat_with_code_interpreter(
        current_user,
        record_id,
        messages,
        request_id,
        api_accessed,
    )


def generate_req_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=7))


@api_tool(
    path="/assistant/create/codeinterpreter",
    name="createCodeInterpreterAssistant",
    method="POST",
    tags=["apiDocumentation"],
    description="""Create a new AgentCore Code Interpreter session.
    Example request:
    {
        "data": {
            "dataSources": ["yourEmail@vanderbilt.edu/2024-05-08/0f20f0447b.json"]
        }
    }

    Example response:
    {
        "success": true,
        "message": "Assistant created successfully.",
        "data": {
            "codeInterpreterRecordId": "yourEmail@vanderbilt.edu/ast/373849029843"
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "dataSources": {
                "type": "array",
                "description": "List of data source IDs to load into the session. Starts with your email.",
                "items": {"type": "string"},
            },
        },
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the session creation was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "codeInterpreterRecordId": {
                        "type": "string",
                        "description": "Unique identifier for this code interpreter record",
                    }
                },
                "required": ["codeInterpreterRecordId"],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@required_env_vars({
    "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_IMAGE_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
})
@validated(op="create")
def create_code_interpreter_assistant(event, context, current_user, name, data):
    extracted_data = data["data"]
    file_keys = extracted_data.get("dataSources", [])
    api_accessed = data["api_accessed"]
    account_id = data["account"] if api_accessed else extracted_data.get("accountId", "")
    request_id = generate_req_id() if api_accessed else extracted_data.get("requestId", generate_req_id())

    return assistants.create_new_assistant(
        user_id=current_user,
        file_keys=file_keys,
        account_id=account_id,
        request_id=request_id,
    )


@api_tool(
    path="/assistant/agentcore/session/delete",
    name="deleteCodeInterpreterAssistant",
    method="DELETE",
    tags=["apiDocumentation"],
    description="""Delete a Code Interpreter assistant instance, permanently removing it from the platform and stopping its underlying AgentCore session.

    Example request (via query parameter):
    DELETE /assistant/agentcore/session/delete?codeInterpreterRecordId=yourEmail@vanderbilt.edu/ast/38940562397049823

    """,
    parameters={
        "type": "object",
        "properties": {
            "codeInterpreterRecordId": {
                "type": "string",
                "description": "Unique identifier of the code interpreter record to delete. Example: 'yourEmail@vanderbilt.edu/ast/38940562397049823'.",
            }
        },
        "required": ["codeInterpreterRecordId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the code interpreter record deletion was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result of the deletion",
            },
        },
        "required": ["success", "message"],
    },
)
@required_env_vars({
    "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.DELETE_ITEM],
})
@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters", {})
    logger.debug("Query params: %s", query_params)
    record_id = query_params.get("codeInterpreterRecordId", "")
    if not record_id or not is_valid_query_param_id(
        record_id, current_user, "ast"
    ):
        return {
            "success": False,
            "message": "Invalid or missing codeInterpreterRecordId parameter",
        }
    logger.info("Deleting code interpreter record: %s", record_id)
    return assistants.delete_record_by_id(record_id, current_user)


@api_tool(
    path="/assistant/files/download/codeinterpreter",
    name="downloadCodeInterpreterFiles",
    method="POST",
    tags=["apiDocumentation"],
    description="""Download files generated by the Code Interpreter assistant via pre-signed URLs.

    Example request:
    {
        "data": {
            "key": "yourEmail@vanderbilt.edu/msg_P0lpFUEY_pie_chart.png"
        }
    }

    Example response:
    {
        "success": true,
        "downloadUrl": "<Download URL>"
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Unique key identifying the file to download. Example: 'yourEmail@vanderbilt.edu/msg_P0lpFUEY_pie_chart.png'. These may be generated in the /assistant/chat/codeinterpreter endpoint responses.",
            },
            "fileName": {
                "type": "string",
                "description": "If specified, directly downloads the file with this name.",
            },
        },
        "required": ["key"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the file download URL generation was successful",
            },
            "downloadUrl": {
                "type": "string",
                "description": "Pre-signed URL for downloading the file",
            },
        },
        "required": ["success", "downloadUrl"],
    },
)
@required_env_vars({
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT],
})
@validated(op="download")
def get_presigned_url_code_interpreter(event, context, current_user, name, data):
    data = data["data"]
    key = data["key"]
    file_name = data.get("fileName", None)

    return assistants.get_presigned_download_url(key, current_user, file_name)


def is_valid_query_param_id(id, current_user, prefix):
    pattern = f"^[^/]+/{re.escape(prefix)}/[0-9a-fA-F-]{{36}}$"
    if re.match(pattern, id):
        return True
    return False
