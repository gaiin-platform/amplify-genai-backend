import uuid
import os
from typing import Callable, Any, get_type_hints
from functools import wraps
from pydantic import BaseModel
import re
from functools import wraps
from typing import Callable, Any, Dict, Type
import yaml
from inspect import signature, Parameter, Signature
from pycommon.api.get_endpoint import get_endpoint, EndpointType
from pycommon.llm.chat import chat

def extract_and_parse_yaml(input_string):
    # Regular expression to extract the YAML block
    yaml_pattern = re.compile(r"```yaml\s*(.*?)\s*```", re.DOTALL)
    match = yaml_pattern.search(input_string)

    if match:
        yaml_content = match.group(1)
        # Parse the YAML content into a dictionary
        parsed_dict = yaml.safe_load(yaml_content)
        return parsed_dict
    else:
        return None


def chat_llm(
    docstring: str,
    system_prompt: str,
    inputs: Dict[str, Any],
    input_instance: BaseModel,
    output_model: Type[BaseModel],
    chat_url: str,
    access_token: str,
    params: Dict[str, Any] = {},
) -> BaseModel:
    # Concatenate input variable names, their values, and their descriptions.
    input_descriptions = "\n".join(
        f"{field}: {getattr(input_instance, field)}"
        + (f" - {field_info.description}" if field_info.description else "")
        for field, field_info in input_instance.__class__.__fields__.items()
    )

    output_descriptions = "\n".join(
        f"{field}:" + (f" {field_info.description}" if field_info.description else "")
        for field, field_info in output_model.__fields__.items()
    )

    prompt_template = f"{system_prompt}\n{docstring}\nInputs:\n{input_descriptions}\nOutputs:\n{output_descriptions}"

    # print(f"Prompt Template: {prompt_template}")

    system_data_prompt = f"""
    Follow the user's instructions very carefully.
    Analyze the task or question and output the requested data.

    You output with the data should be in the YAML format:
    \`\`\`yaml
    thought: <INSERT THOUGHT>
    {output_descriptions}
    \`\`\`
    
    You MUST provide the requested data. Make sure strings are YAML multiline strings
    that properly escape special characters.
    
    You ALWAYS output a \`\`\`yaml code block.
    """

    messages = params.get("messages", [])
    messages.append(
        {
            "role": "system",
            "content": system_data_prompt,
        }
    )
    messages.append(
        {
            "role": "user",
            "content": prompt_template,
            "type": "prompt",
            "data": {},
            "id": str(uuid.uuid4()),
        }
    )

    payload = {
        "model": params.get("model", os.environ.get("AMPLIFY_MODEL", "gpt-4o")),
        "temperature": params.get("temperature", 1.0),
        "max_tokens": params.get("max_tokens", 1000),
        "stream": True,
        "dataSources": params.get("data_sources", []),
        "messages": messages,
        "options": {
            "requestId": params.get("request_id", str(uuid.uuid4())),
            "model": {
                "id": params.get("model", "gpt-4o"),
            },
            "prompt": system_data_prompt,
            "dataSourceOptions": {
                "insertConversationDocuments": params.get(
                    "insert_conversation_documents", False
                ),
                "insertAttachedDocuments": params.get(
                    "insert_attached_documents", True
                ),
                "ragConversationDocuments": params.get(
                    "rag_conversation_documents", True
                ),
                "ragAttachedDocuments": params.get("rag_attached_documents", False),
                "insertConversationDocumentsMetadata": params.get(
                    "insert_conversation_documents_metadata", False
                ),
                "insertAttachedDocumentsMetadata": params.get(
                    "insert_attached_documents_metadata", False
                ),
            },
        },
    }

    # Create an output model instance with the generated prompt template.
    # return output_model(prompt_template=prompt_template)
    response, meta = chat(chat_url, access_token, payload)

    output_data = extract_and_parse_yaml(response)
    result = output_model.parse_obj(output_data)

    return result


def chat_simple(access_token, model, system_instructions, prompt_instructions):

    access_token = access_token if access_token else os.getenv("AMPLIFY_API_KEY")

    if not access_token:
        raise ValueError(
            "You must provide an access token to the function as a keyword arg or set the "
            "environment variable 'AMPLIFY_API_KEY'."
        )

    if not model:
        raise ValueError("You must provide a model to the function as a keyword arg.")

    chat_endpoint = get_endpoint(EndpointType.CHAT_ENDPOINT)
    if not chat_endpoint:
        raise ValueError("Couldnt retrieve 'CHAT_ENDPOINT' from secrets manager.")
    response, meta = chat(
        chat_endpoint,
        access_token,
        {
            "model": model,
            "temperature": 1,
            "max_tokens": 1000,
            "dataSources": [],
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": prompt_instructions},
            ],
            "options": {
                "requestId": str(uuid.uuid4()),
                "model": {
                    "id": model,
                },
                "prompt": system_instructions,
                "ragOnly": True,
            },
        },
    )

    return response


def prompt(system_prompt: str = ""):
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> BaseModel:
            # Get function signature
            sig = signature(func)

            new_params = [
                Parameter(
                    "chat_url", Parameter.KEYWORD_ONLY, annotation=str, default=None
                ),
                Parameter(
                    "access_token",
                    Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=os.getenv("AMPLIFY_TOKEN"),
                ),
                Parameter(
                    "api_key",
                    Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=os.getenv("AMPLIFY_API_KEY"),
                ),
                Parameter(
                    "system_prompt",
                    Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=None,
                ),
                Parameter(
                    "insert_conversation_documents",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=False,
                ),
                Parameter(
                    "insert_attached_documents",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=True,
                ),
                Parameter(
                    "rag_conversation_documents",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=True,
                ),
                Parameter(
                    "rag_attached_documents",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=False,
                ),
                Parameter(
                    "insert_conversation_documents_metadata",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=False,
                ),
                Parameter(
                    "insert_attached_documents_metadata",
                    Parameter.KEYWORD_ONLY,
                    annotation=bool,
                    default=False,
                ),
                Parameter(
                    "model", Parameter.KEYWORD_ONLY, annotation=str, default="gpt-4o"
                ),
                Parameter(
                    "temperature", Parameter.KEYWORD_ONLY, annotation=float, default=1.0
                ),
                Parameter(
                    "max_tokens", Parameter.KEYWORD_ONLY, annotation=int, default=1000
                ),
                Parameter(
                    "stream", Parameter.KEYWORD_ONLY, annotation=bool, default=True
                ),
                Parameter(
                    "data_sources", Parameter.KEYWORD_ONLY, annotation=list, default=[]
                ),
                Parameter(
                    "messages", Parameter.KEYWORD_ONLY, annotation=list, default=None
                ),
                Parameter(
                    "request_id",
                    Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=str(uuid.uuid4()),
                ),
            ]

            updated_params = list(sig.parameters.values())
            for param in new_params:
                if param.name not in sig.parameters:
                    updated_params.append(param)

            # Create a new signature with the updated parameters
            new_sig = Signature(parameters=updated_params)
            func.__signature__ = new_sig

            # Bind arguments
            bound_args = new_sig.bind(*args, **kwargs)
            bound_args.apply_defaults()

            chat_url = kwargs.get("chat_url") or get_endpoint(
                EndpointType.CHAT_ENDPOINT
            )
            access_token = kwargs.get("access_token", os.getenv("AMPLIFY_TOKEN"))
            passed_system_prompt = kwargs.get("system_prompt", None)
            api_key = kwargs.get("api_key", None)
            model = kwargs.get("model", os.getenv("AMPLIFY_DEFAULT_MODEL", None))

            if not chat_url:
                raise ValueError(
                    "You must provide the URL of the Ampllify chat API to @prompt. Please set the "
                    "environment variable 'AMPLIFY_CHAT_URL'."
                )

            if not api_key and not access_token:
                raise ValueError(
                    "You must provide an access token or API key to @prompt. Please set one of the "
                    "environment variables 'AMPLIFY_TOKEN' or 'AMPLIFY_API_KEY'."
                )

            if not model:
                raise ValueError(
                    "You must provide a model to the function as a keyword arg or set the environment "
                    "variable 'AMPLIFY_DEFAULT_MODEL'."
                )

            # Extract the first argument from bound arguments, which should be the input model instance
            input_model_instance = next(iter(bound_args.arguments.values()))

            # Ensure input model instance is a BaseModel
            if not isinstance(input_model_instance, BaseModel):
                raise TypeError("First argument must be a Pydantic BaseModel instance.")

            input_data = input_model_instance.dict()

            # Get function annotations and extract the output model from the return type annotation
            annotations = get_type_hints(func)
            output_model = annotations.get("return")

            if not output_model or not issubclass(output_model, BaseModel):
                raise ValueError(
                    "You must specify a Pydantic BaseModel as the return type in the function annotations."
                )

            # Call the 'chat_llm' function (assuming chat_llm is defined elsewhere)
            result = chat_llm(
                docstring=func.__doc__,
                system_prompt=passed_system_prompt or system_prompt,
                inputs=input_data,
                input_instance=input_model_instance,
                output_model=output_model,
                chat_url=chat_url,
                access_token=access_token or api_key,
                params=kwargs,
            )

            return result

        return wrapper

    return decorator
