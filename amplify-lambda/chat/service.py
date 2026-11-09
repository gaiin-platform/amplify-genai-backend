import requests
from pycommon.llm.chat import chat
from .system_prompts import compose_api_chat_messages, get_prompt_config
import os
from pycommon.api.get_endpoint import get_endpoint, EndpointType
import json
import os
import boto3
from decimal import Decimal
from pycommon.api.ops import api_tool
from pycommon.api.models import get_default_models
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, SecretsManagerOperation
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.CHAT.value])

from pycommon.logger import getLogger
logger = getLogger("chat_endpoint")

class ChatRequestError(Exception):
    """A request-preparation failure whose message is safe to show to the caller."""


def prepare_chat_payload(payload, access_token):
    """Apply Amplify's API-facing request preparation to a chat payload (mutates and returns it).

    Shared by ``/chat`` and the OpenAI-compatible ``/v1/chat/completions`` route so both get
    the same assistant validation, data-source resolution, model alias resolution, system
    prompt composition and deployment policy. ``payload`` must already have the Amplify shape
    (``messages`` plus ``options.model.id``). Raises ChatRequestError for caller mistakes.
    """
    payload_options = payload["options"]
    assistant_id = payload_options.get("assistantId")
    if assistant_id:
        verify_assistant_id = validate_assistant_id(assistant_id, access_token)
        if not verify_assistant_id["success"]:
            logger.error("Invalid assistant id: %s", assistant_id)
            raise ChatRequestError("Invalid assistant id")

    payload["dataSources"] = get_data_source_details(payload.get("dataSources") or [])
    model_id = resolve_model_alias(payload_options["model"]["id"], access_token)
    payload_options["model"]["id"] = model_id
    payload["model"] = model_id
    messages = payload["messages"]

    prompt_config = get_prompt_config()
    base_prompt = prompt_config.get("prompts", {}).get("ordinaryChat.base") or None
    payload["messages"] = compose_api_chat_messages(
        messages,
        request_prompt=payload_options.get("prompt"),
        base_prompt=base_prompt,
    )
    # Apply deployment policy to explicit API options before forwarding. The
    # external CHAT_ENDPOINT remains responsible for its own provider conversion.
    if not prompt_config.get("availability", {}).get("webSearch", False):
        payload["enableWebSearch"] = False
        payload_options["enableWebSearch"] = False
    if not prompt_config.get("availability", {}).get("artifacts", False):
        payload_options["artifacts"] = False
        payload_options["artifactsMode"] = False
    if not prompt_config.get("availability", {}).get("codeInterpreter", False):
        payload_options["codeInterpreterOnly"] = False
    return payload


@api_tool(
    path="/chat",
    name="chatWithAmplify",
    method="POST",
    tags=["apiDocumentation"],
    description="""Interact with Amplify via real-time streaming chat capabilities, utilizing advanced AI models. 
    Example request: 
     {
    "data":{
        "temperature": 0.7,
        "max_tokens": 4000,
        "dataSources": ["yourEmail@vanderbilt.edu/2014-qwertyuio.json"],
        "messages": [
            {
            "role": "user",
            "content": "What is the capital of France?"
            }
        ],
        "options": {
            "ragOnly": false,
            "skipRag": true,
            "model": {"id": "gpt-4o"},
            "assistantId": "astp/abcdefghijk",
            "prompt": "What is the capital of France?"
        }
    }
}""",
    parameters={
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "String representing the model ID. User can request a list of the models by calling the /available_models endpoint",
            },
            "temperature": {
                "type": "number",
                "description": "Float value controlling the randomness of responses. Example: 0.7 for balanced outputs.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Integer representing the maximum number of tokens the model can generate in the response. Typically never over 2048. The user can confirm the max tokens for each model by calling the /available_models endpoint",
            },
            "dataSources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings representing input file ids, primarily used for retrieval-augmented generation (RAG). The user can make a call to the /files/query endpoint to get the id for their file. In the case of uploading a new data source through the /files/upload endpoint, the user can use the returned key as the id.",
            },
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "description": "Array of objects representing the conversation history. Each object includes 'role' (system/assistant/user) and 'content' (the message text). Example: [{'role': 'user', 'content': 'What is the capital of France?'}].",
            },
            "options": {
                "type": "object",
                "properties": {
                    "ragOnly": {
                        "type": "boolean",
                        "description": "Boolean indicating whether only retrieval-augmented responses should be used. Example: false.",
                    },
                    "skipRag": {
                        "type": "boolean",
                        "description": "Boolean indicating whether to skip retrieval-augmented generation. Example: true.",
                    },
                    "assistantId": {
                        "type": "string",
                        "description": "String prefixed with 'astp' to identify the assistant. Example: 'astp/abcdefghijk'.",
                    },
                    "model": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "description": "Object containing model-specific configurations, including 'id'. Example: {'id': 'gpt-4o'}. Must match the model id under the model attribute. Alternatively, use the aliases 'SIMPLE' (resolves to the cheapest available model) or 'COMPLEX' (resolves to the most capable/advanced available model).",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "String representing a system prompt for the model.",
                    },
                },
            },
        },
        "required": ["messages", "options"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the chat request was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "description": "Chat response data from the AI model",
            },
        },
        "required": ["success", "message"],
    },
)
@required_env_vars({
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.BATCH_GET_ITEM],
    "APP_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE], 
})
@validated("chat")
def chat_endpoint(event, context, current_user, name, data):
    access_token = data["access_token"]
    access = data["allowed_access"]
    if APIAccessType.CHAT.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        return {
            "success": False,
            "message": "API key does not have access to chat functionality",
        }
    try:

        chat_endpoint = get_endpoint(EndpointType.CHAT_ENDPOINT)
        if not chat_endpoint:
            return {
                "success": False,
                "message": "We are unable to make the request. Error: No chat endpoint found.",
            }

        payload = data["data"]
        try:
            prepare_chat_payload(payload, access_token)
        except ChatRequestError as e:
            return {"success": False, "message": str(e)}

        response, metadata = chat(chat_endpoint, access_token, payload)
        return {
            "success": True,
            "message": "Chat endpoint response retrieved",
            "data": response,
        }
    except Exception as e:
        return {"success": False, "message": f"Chat service error: {e}"}


def convert_decimal(obj):
    """Convert Decimal objects to Python native types (float or int)"""
    if isinstance(obj, Decimal):
        return float(obj) if obj % 1 != 0 else int(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal(item) for item in obj]
    return obj

_BATCH_GET_LIMIT = 100  # DynamoDB BatchGetItem maximum keys per request
MODEL_ALIAS_SIMPLE = "SIMPLE"
MODEL_ALIAS_COMPLEX = "COMPLEX"
_MODEL_ALIASES = {MODEL_ALIAS_SIMPLE, MODEL_ALIAS_COMPLEX}


def resolve_model_alias(model_id, access_token):
    """Resolve SIMPLE/COMPLEX aliases to their actual model IDs.

    SIMPLE  -> cheapest_model (low-cost, fast model)
    COMPLEX -> advanced_model (highest-capability model)

    If model_id is not a known alias it is returned unchanged.
    If the default-models call fails the original alias string is returned so
    the downstream router can surface the error rather than silently using a
    wrong model.
    """
    if model_id not in _MODEL_ALIASES:
        return model_id

    models = get_default_models(access_token)
    if not models:
        logger.error(
            "Could not resolve model alias '%s': failed to fetch default models",
            model_id,
        )
        return model_id

    # model_id is guaranteed to be one of _MODEL_ALIASES here.
    key = "cheapest_model" if model_id == MODEL_ALIAS_SIMPLE else "advanced_model"
    resolved = models.get(key) or model_id

    logger.debug("Resolved model alias '%s' -> '%s'", model_id, resolved)
    return resolved


def get_data_source_details(data_sources):
    if len(data_sources) == 0:
        return []

    table_name = os.environ[
        "FILES_DYNAMO_TABLE"
    ]  # Get the table name from the environment variable
    dynamodb = boto3.resource("dynamodb")
    data_source_ids = []
    bedrock_kb_sources = []

    for data_source in data_sources:
        id = None
        if isinstance(data_source, str):
            id = data_source
        elif isinstance(data_source, dict) and "id" in data_source:
            id = data_source["id"]

        if id:
            # Pass through Bedrock KB datasources without DynamoDB lookup
            if id.startswith("bedrock-kb://"):
                bedrock_kb_sources.append(data_source if isinstance(data_source, dict) else {"id": id, "type": "bedrock/knowledge-base"})
                continue
            if id.startswith("s3://"):
                id = id.split("s3://")[1]
            data_source_ids.append(id)

    # Nothing to look up (e.g. only Bedrock KB sources): batch_get_item rejects an empty key list.
    if not data_source_ids:
        return bedrock_kb_sources

    # batch_get_item rejects duplicate keys and more than 100 keys per call.
    unique_ids = list(dict.fromkeys(data_source_ids))
    items = []
    for start in range(0, len(unique_ids), _BATCH_GET_LIMIT):
        response = dynamodb.batch_get_item(
            RequestItems={
                table_name: {
                    "Keys": [
                        {"id": collection_id}
                        for collection_id in unique_ids[start:start + _BATCH_GET_LIMIT]
                    ]
                }
            }
        )
        items.extend(response.get("Responses", {}).get(table_name, []))

    # Format the response items as required
    formatted_sources = []
    found_ids = set()
    for item in items:
        found_ids.add(item.get("id", ""))
        # Create metadata object
        metadata = {
            "createdAt": item.get("createdAt", ""),
            "tags": item.get("tags", []),
            "totalTokens": item.get("totalTokens", 0),
            "ragOnly": True,
        }
        id = item.get("id", "")

        # Create formatted object
        formatted_item = {
            "id": "s3://" + id,
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "data": item.get("data", ""),
            "metadata": metadata,
            "key": id,
        }
        formatted_sources.append(formatted_item)

    # Optional: track missing items
    missing_ids = set(data_source_ids) - found_ids
    if missing_ids:
        logger.warning("The following requested IDs were not found: %s", missing_ids)

    # batch_get_item returns items in arbitrary order — re-sort to match the original
    # caller-supplied order so that image labels (#1, #2, ...) map to the correct files.
    id_order = {ds_id: idx for idx, ds_id in enumerate(unique_ids)}
    formatted_sources.sort(key=lambda item: id_order.get(item["key"], len(unique_ids)))

    # Convert any Decimal objects to regular Python types
    formatted_sources = convert_decimal(formatted_sources)

    return formatted_sources + bedrock_kb_sources


def validate_assistant_id(assistant_id, access_token):
    logger.debug("Initiate call to validate assistant id: %s", assistant_id)
    endpoint = os.environ["API_BASE_URL"] + "/assistant/validate/assistant_id"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            data=json.dumps({"data": {"assistantId": assistant_id}}),
        )
        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200:
            logger.error("Error validating assistant id: %s", response.content)
            return {"success": False}
        return response_content

    except Exception as e:
        logger.error("Error validating assistant id: %s", e)
        return {"success": False}
