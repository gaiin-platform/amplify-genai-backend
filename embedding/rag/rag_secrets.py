import json
import os
from pycommon.api.secrets import store_secret_parameter, get_secret_parameter, delete_secret_parameter
from pycommon.encoders import SmartDecimalEncoder

from pycommon.logger import getLogger
logger = getLogger("embedding_rag_secrets")

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
        
    Raises:
        Exception: If storing secrets fails, terminates Lambda execution
    """
    try:
        # Create a safe parameter name from the S3 key
        parameter_name = get_parameter_name(ds_key)
        
        # Convert user_details to JSON string for storage
        secrets_json = json.dumps(user_details, cls=SmartDecimalEncoder)
        
        logger.debug(f"Storing RAG secrets for document: {ds_key} as parameter: {parameter_name}")
        
        # Store the secrets using the existing store_secret_parameter function
        response = store_secret_parameter(parameter_name, secrets_json)
        
        if response:
            logger.info(f"Successfully stored RAG secrets for document: {ds_key}")
            return {"success": True}
        else:
            error_msg = f"Failed to store RAG secrets for document: {ds_key} - store_secret_parameter returned False"
            logger.error(error_msg)
            raise Exception(error_msg)
            
    except Exception as e:
        error_msg = f"Critical error storing RAG secrets for document {ds_key}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


def get_rag_secrets_for_document(ds_key):
    """
    Retrieve RAG-related secrets for a document from Parameter Store.
    
    Args:
        ds_key (str): The S3 key/document identifier (e.g., "user@example.com/2024/document.pdf")
    
    Returns:
        dict: Dictionary containing user details, or success status if not found
        
    Raises:
        Exception: If retrieving secrets fails, terminates Lambda execution
    """
    try:
        parameter_name = get_parameter_name(ds_key)
        
        logger.debug(f"Retrieving RAG secrets for document: {ds_key} from parameter: {parameter_name}")
        
        # Retrieve the secrets using the existing get_secret_parameter function
        secrets_json = get_secret_parameter(parameter_name)
        
        if secrets_json:
            # Parse the JSON string back to dictionary
            user_details = json.loads(secrets_json)
            logger.debug(f"Successfully retrieved RAG secrets for document: {ds_key}")
            return {"success": True, "data": user_details}
        else:
            error_msg = f"No RAG secrets found for document: {ds_key} - document processing cannot continue without credentials"
            logger.error(error_msg)
            raise Exception(error_msg)
            
    except json.JSONDecodeError as e:
        error_msg = f"Critical error parsing RAG secrets JSON for document {ds_key}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Critical error retrieving RAG secrets for document {ds_key}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


def delete_rag_secrets_for_document(ds_key):
    """
    Delete RAG-related secrets for a document from Parameter Store.
    
    Args:
        ds_key (str): The S3 key/document identifier (e.g., "user@example.com/2024/document.pdf")
    
    Returns:
        dict: Dictionary with success status
        
    Raises:
        Exception: If deleting secrets fails, terminates Lambda execution
    """
    try:
        parameter_name = get_parameter_name(ds_key)
        
        logger.debug(f"Deleting RAG secrets for document: {ds_key} from parameter: {parameter_name}")
        
        # Delete the secrets using the existing delete_secret_parameter function
        success = delete_secret_parameter(parameter_name)
        logger.debug(f"Rag secret deleted: {success}")
        
        if success:
            return {"success": True}
        else:
            error_msg = f"Failed to delete RAG secrets for document: {ds_key} - delete_secret_parameter returned False"
            logger.error(error_msg)
            raise Exception(error_msg)
            
    except Exception as e:
        error_msg = f"Critical error deleting RAG secrets for document {ds_key}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)

