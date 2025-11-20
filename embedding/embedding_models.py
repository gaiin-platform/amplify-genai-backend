from decimal import Decimal
import os
import boto3

dynamodb = boto3.resource("dynamodb")


def get_embedding_models():
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    if admin_table is None or model_rate_table is None:
        return {
            "success": False,
            "message": "AMPLIFY_ADMIN_DYNAMODB_TABLE or MODEL_RATE_TABLE not set",
        }

    embedding_model_id = None
    qa_model_id = None
    try:
        print("Getting default model ids from admin table")
        response = admin_table.get_item(Key={"config_id": "defaultModels"})
        if "Item" in response:
            default_models = response["Item"]["data"]
            embedding_model_id = default_models["embeddings"]
            qa_model_id = default_models["cheapest"]
            print("embedding_model_id:", embedding_model_id)
            print("qa_model_id:", qa_model_id)
        else:
            print(f"No Default Models Data Found")
            return {"success": False, "message": "No Default Models Data Found"}
    except Exception as e:
        print(f"Error retrieving default models: {str(e)}")
        return {
            "success": False,
            "message": "Error retrieving default models: {str(e)}",
        }

    if embedding_model_id is None or qa_model_id is None:
        return {"success": False, "message": "Could not find all default models"}

    defaults = {"embedding": None, "qa": None}
    try:

        def get_provider(model_id, default_key):
            response = model_rate_table.get_item(Key={"ModelID": model_id})
            if "Item" in response:
                item = response["Item"]
                defaults[default_key] = {
                    "model_id": item["ModelID"],
                    "provider": item["Provider"],
                }

        get_provider(embedding_model_id, "embedding")
        get_provider(qa_model_id, "qa")

    except Exception as e:
        print(f"Error retrieving default models: {str(e)}")
        return {
            "success": False,
            "message": f"Error retrieving default models: {str(e)}",
        }

    # Check if both default models were found
    if not defaults["embedding"] or not defaults["qa"]:
        print("Could not find all default models")
        return {"success": False, "message": "Could not find all default models"}

    return {"success": True, "data": defaults}
