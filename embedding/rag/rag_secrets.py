import json
import os
from pycommon.api.secrets import store_secret_parameter, get_secret_parameter, delete_secret_parameter
from pycommon.encoders import SmartDecimalEncoder


def get_parameter_name(ds_key):
    """
    Get the parameter name for the RAG secrets.
    """
    safe_key = ds_key.replace("/", "_").replace("@", "_at_").replace(".", "_")
    return f"rag-ds/{os.environ['STAGE']}/{safe_key}"

def store_ds_secrets_for_rag(ds_key, user_details):
    """
    Store RAG-related secrets for a document using Parameter Store.
    
    Args:
        ds_key (str): The S3 key/document identifier (e.g., "user@example.com/2024/document.pdf")
        user_details (dict): Dictionary containing user, account, and api_key information
    
    Returns:
        dict: Dictionary with success status
    """
    try:
        # Create a safe parameter name from the S3 key
        parameter_name = get_parameter_name(ds_key)
        
        # Convert user_details to JSON string for storage
        secrets_json = json.dumps(user_details, cls=SmartDecimalEncoder)
        
        print(f"Storing RAG secrets for document: {ds_key} as parameter: {parameter_name}")
        
        # Store the secrets using the existing store_secret_parameter function
        response = store_secret_parameter(parameter_name, secrets_json)
        
        if response:
            print(f"Successfully stored RAG secrets for document: {ds_key}")
            return {"success": True}
            
    except Exception as e:
        print(f"Error storing RAG secrets for document {ds_key}: {str(e)}")

    print(f"Failed to store RAG secrets for document: {ds_key}")    
    return {"success": False}


def get_rag_secrets_for_document(ds_key):
    """
    Retrieve RAG-related secrets for a document from Parameter Store.
    
    Args:
        ds_key (str): The S3 key/document identifier (e.g., "user@example.com/2024/document.pdf")
    
    Returns:
        dict: Dictionary containing user details, or success status if not found
    """
    try:
        parameter_name = get_parameter_name(ds_key)
        
        print(f"Retrieving RAG secrets for document: {ds_key} from parameter: {parameter_name}")
        
        # Retrieve the secrets using the existing get_secret_parameter function
        secrets_json = get_secret_parameter(parameter_name)
        
        if secrets_json:
            # Parse the JSON string back to dictionary
            user_details = json.loads(secrets_json)
            print(f"Successfully retrieved RAG secrets for document: {ds_key}")
            return {"success": True, "data": user_details}
        print(f"No RAG secrets found for document: {ds_key}")
            
    except json.JSONDecodeError as e:
        print(f"Error parsing RAG secrets JSON for document {ds_key}: {str(e)}")
    except Exception as e:
        print(f"Error retrieving RAG secrets for document {ds_key}: {str(e)}")

    return {"success": False}


def delete_rag_secrets_for_document(ds_key):
    """
    Delete RAG-related secrets for a document from Parameter Store.
    
    Args:
        ds_key (str): The S3 key/document identifier (e.g., "user@example.com/2024/document.pdf")
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        parameter_name = get_parameter_name(ds_key)
        
        print(f"Deleting RAG secrets for document: {ds_key} from parameter: {parameter_name}")
        
        # Delete the secrets using the existing delete_secret_parameter function
        success = delete_secret_parameter(parameter_name)
        print(f"Rag secret deleted: {success}")
        return {"success": success}
            
    except Exception as e:
        print(f"Error deleting RAG secrets for document {ds_key}: {str(e)}")
    return {"success": False}

