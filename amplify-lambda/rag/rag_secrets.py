import json
import os
import boto3
from pycommon.api.secrets import store_secret_parameter, get_secret_parameter, delete_secret_parameter
from pycommon.encoders import SmartDecimalEncoder

from pycommon.logger import getLogger
logger = getLogger("rag_secrets")

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
        
        logger.info("Storing RAG secrets for document: %s as parameter: %s", ds_key, parameter_name)
        
        # Store the secrets using the existing store_secret_parameter function
        response = store_secret_parameter(parameter_name, secrets_json)
        
        if response:
            logger.info("Successfully stored RAG secrets for document: %s", ds_key)
            return {"success": True}
        else:
            error_msg = f"Failed to store RAG secrets for document: {ds_key} - store_secret_parameter returned False"
            logger.error("Failed to store RAG secrets for document: %s - store_secret_parameter returned False", ds_key)
            raise Exception(error_msg)
            
    except Exception as e:
        error_msg = f"Critical error storing RAG secrets for document {ds_key}: {str(e)}"
        logger.error("Critical error storing RAG secrets for document %s: %s", ds_key, str(e))
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
        
        logger.debug("Retrieving RAG secrets for document: %s from parameter: %s", ds_key, parameter_name)
        
        # Retrieve the secrets using the existing get_secret_parameter function
        secrets_json = get_secret_parameter(parameter_name)
        
        if secrets_json:
            # Parse the JSON string back to dictionary
            user_details = json.loads(secrets_json)
            logger.debug("Successfully retrieved RAG secrets for document: %s", ds_key)
            return {"success": True, "data": user_details}
        else:
            error_msg = f"No RAG secrets found for document: {ds_key} - document processing cannot continue without credentials"
            logger.error("No RAG secrets found for document: %s - document processing cannot continue without credentials", ds_key)
            raise Exception(error_msg)
            
    except json.JSONDecodeError as e:
        error_msg = f"Critical error parsing RAG secrets JSON for document {ds_key}: {str(e)}"
        logger.error("Critical error parsing RAG secrets JSON for document %s: %s", ds_key, str(e))
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Critical error retrieving RAG secrets for document {ds_key}: {str(e)}"
        logger.error("Critical error retrieving RAG secrets for document %s: %s", ds_key, str(e))
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
        
        logger.info("Deleting RAG secrets for document: %s from parameter: %s", ds_key, parameter_name)
        
        # Delete the secrets using the existing delete_secret_parameter function
        success = delete_secret_parameter(parameter_name)
        logger.debug("Rag secret deleted: %s", success)
        
        if success:
            return {"success": True}
        else:
            error_msg = f"Failed to delete RAG secrets for document: {ds_key} - delete_secret_parameter returned False"
            logger.error("Failed to delete RAG secrets for document: %s - delete_secret_parameter returned False", ds_key)
            raise Exception(error_msg)
            
    except Exception as e:
        error_msg = f"Critical error deleting RAG secrets for document {ds_key}: {str(e)}"
        logger.error("Critical error deleting RAG secrets for document %s: %s", ds_key, str(e))
        raise Exception(error_msg)


def cleanup_missed_rag_secrets():
    """
    Clean up orphaned RAG secrets from Parameter Store.
    This function is designed to run daily to remove any RAG secrets
    that weren't properly cleaned up during normal processing.
    
    Returns:
        dict: Summary of cleanup operation
    """
    try:
        ssm = boto3.client('ssm')
        stage = os.environ.get('STAGE', 'dev')
        prefix = f"/rag-ds/{stage}/"
        
        logger.info("Starting cleanup of RAG secrets with prefix: %s", prefix)
        
        # Get all parameters with the RAG prefix using get_parameters_by_path
        paginator = ssm.get_paginator('get_parameters_by_path')
        page_iterator = paginator.paginate(
            Path=prefix,
            Recursive=True
        )
        
        deleted_count = 0
        error_count = 0
        
        for page in page_iterator:
            parameters = page.get('Parameters', [])
            
            for param in parameters:
                param_name = param['Name']
                try:
                    logger.info("Deleting orphaned RAG secret: %s", param_name)
                    
                    # Delete the parameter
                    ssm.delete_parameter(Name=param_name)
                    deleted_count += 1
                    
                except Exception as delete_error:
                    logger.error("Failed to delete parameter %s: %s", param_name, str(delete_error))
                    error_count += 1
        
        result = {
            "success": True,
            "deleted_count": deleted_count,
            "error_count": error_count,
            "message": f"Cleanup completed. Deleted {deleted_count} parameters, {error_count} errors."
        }
        
        logger.info("RAG secrets cleanup completed: %s", result)
        return result
        
    except Exception as e:
        error_msg = f"Error during RAG secrets cleanup: {str(e)}"
        logger.error("%s", error_msg)
        return {
            "success": False,
            "deleted_count": 0,
            "error_count": 0,
            "message": error_msg
        }


def lambda_handler(event, context):
    """
    Lambda handler for the cleanup function.
    """
    try:
        result = cleanup_missed_rag_secrets()
        
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
        
    except Exception as e:
        logger.error("Lambda handler error: %s", str(e))
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'message': f'Lambda execution failed: {str(e)}'
            })
        }