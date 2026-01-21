"""
Critical Error Tracker Service

This module provides functionality to track and manage critical errors across
the entire application. It includes:
- Admin-only API endpoints (@validated decorator) for viewing and resolving errors
- Query and status update functions for internal use
- Integration with DynamoDB for persistence

Note: The log_critical_error function has been moved to pycommon.api.critical_logging
      to allow all Lambda functions to access it without admin dependencies.
"""

import os
import time
import traceback
from typing import Optional, Dict, List, Any
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.authz import validated, setup_validated, add_api_access_types
from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.api.critical_logging import log_critical_error
from pycommon.const import APIAccessType
from pycommon.logger import getLogger

from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

# Setup
logger = getLogger("critical_error_tracker")
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ADMIN.value])

# DynamoDB setup
dynamodb = boto3.resource("dynamodb")
critical_errors_table = dynamodb.Table(os.environ["CRITICAL_ERRORS_DYNAMODB_TABLE"])

# Constants
STATUS_ACTIVE = "ACTIVE"
STATUS_RESOLVED = "RESOLVED"
STATUS_RETURNED = "RETURNED"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

# Default pagination limit
DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 100


# Utility function for Decimal to float conversion
def convert_decimals_to_float(obj: Any) -> Any:
    """
    Recursively convert Decimal values to float for JSON serialization.
    
    When reading from DynamoDB, numbers come back as Decimal. We convert
    them to float/int for proper JSON serialization.
    
    Uses SmartDecimalEncoder logic: converts to int if whole number, float otherwise.
    
    Args:
        obj: Object to convert (can be dict, list, Decimal, or other)
    
    Returns:
        Converted object with Decimals as float/int
    """
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    elif isinstance(obj, dict):
        return {key: convert_decimals_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals_to_float(item) for item in obj]
    else:
        return obj


# Basic CRUD Operations (NO @validated decorator)
# Note: log_critical_error has been moved to pycommon.api.critical_logging

@required_env_vars({
    "CRITICAL_ERRORS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM]
})
def get_critical_error_by_id(error_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a specific critical error by its ID.
    
    Args:
        error_id: UUID of the error to retrieve
    
    Returns:
        Dict with error details if found, None otherwise
    """
    try:
        response = critical_errors_table.get_item(
            Key={"error_id": error_id}
        )
        
        if "Item" in response:
            item = response["Item"]
            # Convert Decimal back to float for JSON serialization
            item = convert_decimals_to_float(item)
            logger.debug("Retrieved error: %s", error_id)
            return item
        else:
            logger.warning("Error not found: %s", error_id)
            return None
            
    except ClientError as e:
        logger.error("DynamoDB error retrieving error %s: %s", error_id, str(e))
        return None
    except Exception as e:
        logger.error("Error retrieving error %s: %s", error_id, str(e))
        return None


@required_env_vars({
    "CRITICAL_ERRORS_DYNAMODB_TABLE": [DynamoDBOperation.UPDATE_ITEM]
})
def update_critical_error_status(
    error_id: str,
    status: str,
    resolved_by: str,
    resolution_notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update the status of a critical error.
    
    Args:
        error_id: UUID of the error to update
        status: New status (typically "RESOLVED")
        resolved_by: Username of admin resolving it
        resolution_notes: Optional notes about resolution
    
    Returns:
        Dict with success status
    """
    try:
        resolved_timestamp = int(time.time())
        
        # Build update expression dynamically
        update_expression = "SET #status = :status, resolved_by = :resolved_by, resolved_at = :resolved_at"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":resolved_by": resolved_by,
            ":resolved_at": resolved_timestamp
        }
        
        # Add resolution notes if provided
        if resolution_notes:
            update_expression += ", resolution_notes = :notes"
            expression_attribute_values[":notes"] = resolution_notes
        
        # Set TTL for 90 days after resolution (for auto-cleanup)
        if status == STATUS_RESOLVED:
            ttl_timestamp = resolved_timestamp + (90 * 24 * 60 * 60)  # 90 days
            update_expression += ", #ttl = :ttl"
            expression_attribute_names["#ttl"] = "ttl"  # Escape reserved keyword
            expression_attribute_values[":ttl"] = ttl_timestamp
        
        # Perform update
        critical_errors_table.update_item(
            Key={"error_id": error_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression="attribute_exists(error_id)"  # Ensure item exists
        )
        
        logger.info(
            "Error %s updated to %s by %s",
            error_id,
            status,
            resolved_by
        )
        
        return {
            "success": True,
            "message": "Error status updated successfully"
        }
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            error_msg = f"Error {error_id} not found"
            logger.warning(error_msg)
            return {"success": False, "message": error_msg}
        else:
            error_msg = f"DynamoDB error: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "message": error_msg}
            
    except Exception as e:
        error_msg = f"Error updating status: {str(e)}"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}


@required_env_vars({
    "CRITICAL_ERRORS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY]
})
def query_critical_errors(
    status: str = STATUS_ACTIVE,
    limit: int = DEFAULT_QUERY_LIMIT,
    last_evaluated_key: Optional[Dict[str, Any]] = None,
    scan_forward: bool = False
) -> Dict[str, Any]:
    """
    Query critical errors by status with pagination.
    
    Uses the status-timestamp-index GSI for efficient querying.
    Results are sorted by timestamp (newest first by default).
    
    Args:
        status: Status to filter by (ACTIVE or RESOLVED)
        limit: Maximum number of items to return
        last_evaluated_key: Pagination token from previous query
        scan_forward: If False (default), newest items first
    
    Returns:
        Dict with items, count, and pagination info
    """
    try:
        # Enforce max limit
        if limit > MAX_QUERY_LIMIT:
            limit = MAX_QUERY_LIMIT
        
        # Build query parameters
        query_params = {
            "IndexName": "status-timestamp-index",
            "KeyConditionExpression": Key("status").eq(status),
            "Limit": limit,
            "ScanIndexForward": scan_forward  # False = descending (newest first)
        }
        
        # Add pagination token if provided
        if last_evaluated_key:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        
        # Execute query
        response = critical_errors_table.query(**query_params)
        
        # Extract results
        items = response.get("Items", [])
        
        # Convert Decimal to float for JSON serialization
        items = [convert_decimals_to_float(item) for item in items]
        
        # Build response
        result = {
            "success": True,
            "items": items,
            "count": len(items),
            "has_more": "LastEvaluatedKey" in response
        }
        
        # Include pagination token if there are more results
        if "LastEvaluatedKey" in response:
            result["last_evaluated_key"] = response["LastEvaluatedKey"]
        
        logger.debug(
            "Queried %d errors with status=%s, has_more=%s",
            len(items),
            status,
            result["has_more"]
        )
        
        return result
        
    except ClientError as e:
        error_msg = f"DynamoDB query error: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": error_msg,
            "items": [],
            "count": 0
        }
    except Exception as e:
        error_msg = f"Error querying critical errors: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": error_msg,
            "items": [],
            "count": 0
        }


def get_all_active_errors(limit: int = DEFAULT_QUERY_LIMIT) -> Dict[str, Any]:
    """
    Convenience function to get all ACTIVE critical errors.
    
    Args:
        limit: Maximum number of items to return
    
    Returns:
        Dict with items and pagination info (same as query_critical_errors)
    """
    return query_critical_errors(
        status=STATUS_ACTIVE,
        limit=limit,
        last_evaluated_key=None,
        scan_forward=False
    )


# Admin API Endpoints (WITH @validated decorator)

@validated(op="read")
def get_critical_errors_admin(event, context, current_user, name, data):
    """
    ADMIN API ENDPOINT: Get critical errors with filtering and pagination.
    
    This endpoint requires admin authentication.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        current_user: Username from validated decorator
        name: Function name from validated decorator
        data: Validated request data
    
    Returns:
        Dict with errors list and pagination info
    """
    logger.info("Admin %s requesting critical errors", current_user)
    
    try:
        # Verify admin authentication
        access_token = data.get("access_token")
        if not access_token:
            logger.warning("No access token provided")
            return {"success": False, "error": "Access token required"}
        
        if not verify_user_as_admin(access_token, "view_critical_errors"):
            logger.warning("User %s failed admin verification", current_user)
            return {"success": False, "error": "Unable to authenticate user as admin"}
        
        # Extract query parameters
        request_data = data.get("data", {})
        limit = request_data.get("limit", DEFAULT_QUERY_LIMIT)
        last_evaluated_key = request_data.get("last_evaluated_key")
        
        # Query both ACTIVE and RETURNED errors (need to show errors that came back)
        # We need to query both statuses and merge results since GSI only allows single partition key
        active_result = query_critical_errors(
            status=STATUS_ACTIVE,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_forward=False  # Newest first
        )
        
        returned_result = query_critical_errors(
            status=STATUS_RETURNED,
            limit=limit,
            last_evaluated_key=None,  # Separate pagination for RETURNED
            scan_forward=False  # Newest first
        )
        
        if not active_result.get("success") or not returned_result.get("success"):
            return {
                "success": False,
                "error": "Error querying critical errors"
            }
        
        # Merge results and sort by timestamp (newest first)
        all_errors = active_result["items"] + returned_result["items"]
        all_errors.sort(key=lambda x: x.get("last_occurrence", x.get("timestamp", 0)), reverse=True)
        
        # Trim to requested limit
        errors_to_return = all_errors[:limit]
        
        # Check if there are more results
        has_more = active_result.get("has_more", False) or len(all_errors) > limit
        
        # Build response
        response = {
            "success": True,
            "errors": errors_to_return,
            "count": len(errors_to_return),
            "has_more": has_more
        }
        
        # Include pagination token if present (only for ACTIVE since we're merging)
        if active_result.get("has_more") and "last_evaluated_key" in active_result:
            response["last_evaluated_key"] = active_result["last_evaluated_key"]
        
        logger.info(
            "Admin %s retrieved %d errors (%d ACTIVE, %d RETURNED)",
            current_user,
            len(errors_to_return),
            len(active_result["items"]),
            len(returned_result["items"])
        )
        
        return response
        
    except Exception as e:
        error_msg = f"Error in get_critical_errors_admin: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": "Internal server error"
        }


@validated(op="update")
def resolve_critical_error_admin(event, context, current_user, name, data):
    """
    ADMIN API ENDPOINT: Mark a critical error as resolved.
    
    This endpoint requires admin authentication.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        current_user: Username from validated decorator
        name: Function name from validated decorator
        data: Validated request data
    
    Returns:
        Dict with success status
    """
    logger.info("Admin %s attempting to resolve critical error", current_user)
    
    try:
        # Verify admin authentication
        access_token = data.get("access_token")
        if not access_token:
            logger.warning("No access token provided")
            return {"success": False, "error": "Access token required"}
        
        if not verify_user_as_admin(access_token, "resolve_critical_errors"):
            logger.warning("User %s failed admin verification", current_user)
            return {"success": False, "error": "Unable to authenticate user as admin"}
        
        # Extract request data
        request_data = data.get("data", {})
        error_id = request_data.get("error_id")
        resolution_notes = request_data.get("resolution_notes", "")
        
        # Validate error_id presence
        if not error_id:
            return {"success": False, "error": "error_id is required"}
        
        # First, verify the error exists and is ACTIVE
        existing_error = get_critical_error_by_id(error_id)
        if not existing_error:
            logger.warning("Error %s not found", error_id)
            return {
                "success": False,
                "error": f"Error {error_id} not found"
            }
        
        if existing_error.get("status") == STATUS_RESOLVED:
            logger.info("Error %s already resolved", error_id)
            return {
                "success": False,
                "error": "Error is already resolved"
            }
        
        # Update the error status
        update_result = update_critical_error_status(
            error_id=error_id,
            status=STATUS_RESOLVED,
            resolved_by=current_user,
            resolution_notes=resolution_notes
        )
        
        if not update_result.get("success"):
            return {
                "success": False,
                "error": update_result.get("message", "Failed to update error")
            }
        
        logger.info(
            "Admin %s resolved error %s",
            current_user,
            error_id
        )
        
        return {
            "success": True,
            "message": "Critical error resolved successfully",
            "error_id": error_id
        }
        
    except Exception as e:
        error_msg = f"Error in resolve_critical_error_admin: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": "Internal server error"
        }
