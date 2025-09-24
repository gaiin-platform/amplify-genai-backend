from datetime import datetime, timezone
from decimal import Decimal
import os
import boto3
from model_rates.update_table import load_model_rate_table, get_csv_model_ids
from pycommon.api.amplify_groups import get_user_affiliated_groups
from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.api.ops import api_tool

from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
add_api_access_types([APIAccessType.ADMIN.value, APIAccessType.CHAT.value])

setup_validated(rules, get_permission_checker)
dynamodb = boto3.resource("dynamodb")
COST_FIELDS = [
    "OutputCostPerThousandTokens",
    "InputCostPerThousandTokens",
    "CachedCostPerThousandTokens",
]
DEFAULT_MODELS = "defaultModels"


@api_tool(
    path="/available_models",
    name="getUserAvailableModels",
    method="GET",
    tags=["apiDocumentation"],
    description="""Retrieve a list of available AI models for the user, including details such as model ID, name, description, and capabilities.

    Example response:
    {
        "success": true,
        "data": {
            # list of Model dicts. example
            "models": [
                {
                    "id": "gpt-4o",
                    "name": "GPT-4o",
                    "description": "An optimized version of GPT-4 for general use.",
                    "inputContextWindow": 200000,
                    "outputTokenLimit": 4096, 
                    "supportsImages": true,
                    "supportsReasoning": false,
                    "provider": "OpenAI",
                    "supportsSystemPrompts": true,
                    "systemPrompt": "Additional Prompt",
                },
            ],
            "default": <Model dict>,
            "advanced": <Model dict>,
            "cheapest": <Model dict>
        }
    }
    """,
    parameters={"type": "object", "properties": {}, "required": []},
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
                    "models": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Model identifier",
                                },
                                "name": {
                                    "type": "string",
                                    "description": "Human-readable model name",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Model description",
                                },
                                "inputContextWindow": {
                                    "type": "integer",
                                    "description": "Maximum input context window size",
                                },
                                "outputTokenLimit": {
                                    "type": "integer",
                                    "description": "Maximum output token limit",
                                },
                                "supportsImages": {
                                    "type": "boolean",
                                    "description": "Whether the model supports image inputs",
                                },
                                "supportsReasoning": {
                                    "type": "boolean",
                                    "description": "Whether the model supports reasoning capabilities",
                                },
                                "provider": {
                                    "type": "string",
                                    "description": "Model provider name",
                                },
                                "supportsSystemPrompts": {
                                    "type": "boolean",
                                    "description": "Whether the model supports system prompts",
                                },
                                "systemPrompt": {
                                    "type": "string",
                                    "description": "Additional system prompt text",
                                },
                            },
                        },
                        "description": "List of available models",
                    },
                    "default": {
                        "type": "object",
                        "description": "Default model configuration",
                    },
                    "advanced": {
                        "type": "object",
                        "description": "Advanced model configuration",
                    },
                    "cheapest": {
                        "type": "object",
                        "description": "Most cost-effective model configuration",
                    },
                },
                "required": ["models"],
            },
        },
        "required": ["success", "data"],
    },
)
@validated(op="read")
def get_user_available_models(event, context, current_user, name, data):
    # Retrieve supported models
    supported_models_result = get_supported_models()

    if not supported_models_result.get("success"):
        return supported_models_result

    supported_models = supported_models_result.get("data", {}).items()
    affiliated_groups, _ = get_user_affiliated_groups(data["access_token"])
    print("User affiliated_groups: ", affiliated_groups)

    # Filter and format the available models directly using a list comprehension
    available_models = [
        extract_data(model_id, model_data)
        for model_id, model_data in supported_models
        if (model_data.get("isAvailable", False)
            or bool(set(model_data.get("exclusiveGroupAvailability", [])) & set(affiliated_groups or []))
        )
    ]
    
    # print("Available user models:", available_models)

    default_results = get_admin_default_models()
    # setting as None if not found
    default_model = None
    advanced_model = None
    cheapest_model = None
    # print("default_results: ", default_results)
    if not default_results or len(default_results.keys()) == 0:
        default_model, advanced_model, cheapest_model, _ = (
            extract_and_update_default_models()
        )
    else:
        default_model_types = {
            "user": None,
            "advanced": None,
            "cheapest": None,
            "documentCaching": None,
        }
        available_models_by_id = {model["id"]: model for model in available_models}

        # Process all default model types in a loop
        for model_type in default_model_types.keys():
            model_id = default_results.get(model_type)
            if model_id and model_id in available_models_by_id:
                default_model_types[model_type] = available_models_by_id[model_id]
            print(f"{model_type}_model: {default_model_types[model_type]}")

        # Assign variables from the dictionary
        default_model = default_model_types["user"]
        advanced_model = default_model_types["advanced"]
        cheapest_model = default_model_types["cheapest"]
        document_caching_model = default_model_types["documentCaching"]

    return {
        "success": True,
        "data": {
            "models": available_models,
            "default": default_model,
            "advanced": advanced_model,
            "cheapest": cheapest_model,
            "documentCaching": document_caching_model,
        },
    }


# to seamlessly update to the new form of saving default models - over time this will not be needed
def extract_and_update_default_models():
    print("Directly querying for default models in table")
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])

    # Map DB fields to result types
    field_mappings = {
        "UsersDefault": "user",
        "DefaultAdvancedModel": "advanced",
        "DefaultCheapestModel": "cheapest",
        "DefaultEmbeddingsModel": "embeddings",
        "DefaultAgentModel": "agent",
        "DefaultDocumentCachingModel": "documentCaching",
    }

    results = {model_type: None for model_type in field_mappings.values()}

    try:
        # Query each default model type
        for db_field, model_type in field_mappings.items():
            # Scan with filter for each default type
            response = model_rate_table.scan(
                FilterExpression=f"attribute_exists({db_field}) AND {db_field} = :true_val",
                ExpressionAttributeValues={":true_val": True},
            )

            # Get the first matching model (should be only one)
            items = response.get("Items", [])
            if items:
                model_data = items[0]
                model_id = model_data.get("ModelID")

                # Transform to our internal format and extract data
                transformed_model = model_transform_db_to_internal(model_data)
                results[model_type] = extract_data(model_id, transformed_model)
                print(f"Found {model_type} model: {model_id}")

        # Update the admin table with the found defaults
        if any(model is not None for model in results.values()):
            print("Updating default models in admin table")
            admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])

            default_models_data = {
                model_type: (
                    results[model_type].get("id") if results[model_type] else None
                )
                for model_type in results
            }

            # Put the data in the admin table
            admin_table.put_item(
                Item={
                    "config_id": DEFAULT_MODELS,
                    "data": default_models_data,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )

    except Exception as e:
        print(f"Error querying/updating default models: {str(e)}")
        raise e

    return (
        results["user"],
        results["advanced"],
        results["cheapest"],
        results["embeddings"],
    )


def extract_data(model_id, model_data):
    return {
        "id": model_id,
        "name": model_data["name"],
        "description": model_data.get("description", ""),
        "inputContextWindow": model_data.get("inputContextWindow", -1),
        "outputTokenLimit": model_data.get("outputTokenLimit", -1),
        "supportsImages": model_data.get("supportsImages", False),
        "supportsReasoning": model_data.get("supportsReasoning", False),
        "provider": model_data.get("provider", ""),
        "supportsSystemPrompts": model_data.get("supportsSystemPrompts", False),
        "systemPrompt": model_data.get("systemPrompt", ""),
        "inputTokenCost": model_data.get("inputTokenCost", 0),
        "outputTokenCost": model_data.get("outputTokenCost", 0),
        "cachedTokenCost": model_data.get("cachedTokenCost", 0),
    }


@validated(op="read")
def get_supported_models_as_admin(event, context, current_user, name, data):
    if data["api_accessed"] and "admin" not in data["allowed_access"]:
        return {
            "success": False,
            "message": "API key does not have access to admin functionality",
        }

    if not verify_user_as_admin(data["access_token"], "Get Supported Models"):
        return {"success": False, "error": "Unable to authenticate user as admin"}
    model_result = get_supported_models()
    current_model_data = model_result.get("data", {})
    # if there was an error or there were models in the table then we can return the value like normal
    # otherwise we need to run the fill model table
    if not model_result["success"] or models_are_current(current_model_data):
        return model_result

    print("Models are not popluated or are outdated")
    if current_model_data:
        current_model_data = {
            model["id"]: adjust_data_to_decimal(model_transform_internal_to_db(model))
            for model in model_result.get("data", {}).values()
        }

    load_model_rate_table(current_model_data)
    return get_supported_models()


def models_are_current(models_data):
    if not models_data:
        return False

    csv_model_ids = get_csv_model_ids()
    models_data_ids = {
        model.get("id") for model in models_data.values() if model.get("id") is not None
    }

    # Verify that all CSV model IDs are present in the models_data
    return csv_model_ids.issubset(models_data_ids)


def is_model_current(model_data):
    if not model_data:
        return False

    required_cols_set = set(dynamodb_to_internal_field_map.values())
    existing_columns = set(model_data.keys()) if model_data else set()
    return required_cols_set.issubset(existing_columns)


def get_supported_models():
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    models_data = []
    try:
        # Retrieve all items from the DynamoDB table
        response = model_rate_table.scan()
        models_data = response.get("Items", [])

        # Check if there are more items (pagination)
        while "LastEvaluatedKey" in response:
            response = model_rate_table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            models_data.extend(response.get("Items", []))
    except Exception as e:
        return {"success": False, "message": f"Error retrieving model data: {str(e)}"}

    # Transform data into the desired structure
    supported_models_config = {}
    for model in models_data:
        model_id = model.get("ModelID")

        # Convert Decimal fields to floats before creating your transformed model
        for field in COST_FIELDS:
            if field in model and isinstance(model[field], Decimal):
                model[field] = float(model[field])

        transformed_model = model_transform_db_to_internal(model)
        # filter out outdated models
        if is_model_current(transformed_model):
            supported_models_config[model_id] = transformed_model
        else:
            print("Skipping outdated model: ", model_id)

    return {"success": True, "data": supported_models_config}


def adjust_data_to_decimal(model):
    for field in COST_FIELDS:
        if field in model and model[field] is not None:
            # Handle both float and string values
            if isinstance(model[field], (float, str)):
                try:
                    model[field] = Decimal(str(model[field]))
                except (ValueError, TypeError) as e:
                    print(f"Warning: Could not convert {field} value '{model[field]}' to Decimal: {e}")
    return model


@validated(op="update")
def update_supported_models(event, context, current_user, name, data):
    if not verify_user_as_admin(data["access_token"], "Update Supported Models"):
        return {"success": False, "error": "Unable to authenticate user as admin"}

    updated_model_data = data["data"]["models"]
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])

    try:
        # Retrieve Existing Models
        response = model_rate_table.scan()
        existing_models = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = model_rate_table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            existing_models.extend(response.get("Items", []))
    except Exception as e:
        print(f"Error retrieving existing models: {str(e)}")
        return {
            "success": False,
            "message": f"Error retrieving existing models: {str(e)}",
        }
    # dict for existing models keyed by ModelID to match update_data
    existing_models_dict = {model["ModelID"]: model for model in existing_models}
    new_models_dict = {
        model["id"]: model_transform_internal_to_db(model)
        for model in updated_model_data.values()
    }

    # Identify Models to Add, Update, or Delete
    existing_model_ids = set(existing_models_dict.keys())
    new_model_ids = set(new_models_dict.keys())

    # Models to delete (in existing but not in new)
    models_to_delete = existing_model_ids - new_model_ids
    print("delete: ", models_to_delete)

    # Models to add (in new but not in existing)
    models_to_add = new_model_ids - existing_model_ids
    print("add: ", models_to_add)

    # Models to update (in both existing and new)
    models_to_update = existing_model_ids & new_model_ids
    print("update: ", models_to_update)

    try:
        # Batch Write Operations
        with model_rate_table.batch_writer() as batch:
            for model_id in models_to_delete:
                batch.delete_item(Key={"ModelID": model_id})

            for model_id in models_to_add:
                updated_model = adjust_data_to_decimal(new_models_dict[model_id])
                batch.put_item(Item=updated_model)

            for model_id in models_to_update:
                # if Data has changed, update the model
                if not models_are_equal(
                    existing_models_dict[model_id], new_models_dict[model_id]
                ):
                    print("updating model: ", model_id)
                    updated_model = adjust_data_to_decimal(new_models_dict[model_id])
                    batch.put_item(Item=updated_model)
    except Exception as e:
        print(f"Error batch writing models: {str(e)}")
        return {"success": False, "message": f"Error batch writing models: {str(e)}"}

    return {"success": True, "message": "Model configurations updated successfully."}


# Mapping from DynamoDB field names to internal field names
dynamodb_to_internal_field_map = {
    "ModelID": "id",
    "ModelName": "name",
    "Provider": "provider",
    "InputContextWindow": "inputContextWindow",
    "OutputTokenLimit": "outputTokenLimit",
    "OutputCostPerThousandTokens": "outputTokenCost",
    "InputCostPerThousandTokens": "inputTokenCost",
    "CachedCostPerThousandTokens": "cachedTokenCost",
    "Description": "description",
    "ExclusiveGroupAvailability": "exclusiveGroupAvailability",
    "SupportsImages": "supportsImages",
    "SupportsReasoning": "supportsReasoning",
    "Available": "isAvailable",
    "Built-In": "isBuiltIn",
    "SupportsSystemPrompts": "supportsSystemPrompts",
    "AdditionalSystemPrompt": "systemPrompt",
}


def model_transform_db_to_internal(model):
    transformed = {}
    for dynamodb_key, internal_key in dynamodb_to_internal_field_map.items():
        # Only include this key if it's actually present in the DynamoDB item
        if dynamodb_key in model:
            transformed[internal_key] = model[dynamodb_key]
    return transformed


def model_transform_internal_to_db(model):
    # Reverse mapping
    internal_to_dynamodb_field_map = {
        v: k for k, v in dynamodb_to_internal_field_map.items()
    }

    transformed = {}
    for internal_key, dynamodb_key in internal_to_dynamodb_field_map.items():
        # Only include if it's actually present in `model`
        if internal_key in model:
            transformed[dynamodb_key] = model[internal_key]
    return transformed


def models_are_equal(existing_model, new_model):
    keys_to_compare = set(existing_model.keys()) | set(new_model.keys())

    # Float keys to compare with tolerance
    float_keys = set(COST_FIELDS)
    tolerance = 1e-6  # Adjust the tolerance as needed

    for key in keys_to_compare:
        existing_value = existing_model.get(key)
        new_value = new_model.get(key)

        if key in float_keys:
            # Convert to float and compare with tolerance
            existing_float = (
                float(existing_value) if existing_value is not None else 0.0
            )
            new_float = float(new_value) if new_value is not None else 0.0
            if abs(existing_float - new_float) > tolerance:
                return False
        else:
            if existing_value != new_value:
                return False
    return True


@validated(op="read")
def get_default_models(event, context, current_user, name, data):
    default_models_result = get_admin_default_models()

    if default_models_result:
        # Filter out models with null values
        filtered_models = {
            k: v for k, v in default_models_result.items() if v is not None
        }
        return {"success": True, "data": filtered_models}
    else:
        return {"success": False, "message": "Error retrieving default models"}


def get_admin_default_models():
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])
    try:
        print("Getting default model ids from admin table")
        response = admin_table.get_item(Key={"config_id": DEFAULT_MODELS})
        if "Item" in response:
            return response["Item"]["data"]
        else:
            print(f"No Default Models Data Found")
            return {}
    except Exception as e:
        print(f"Error retrieving default models: {str(e)}")
    return None
