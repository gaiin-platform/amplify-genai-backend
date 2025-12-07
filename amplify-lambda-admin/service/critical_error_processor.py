"""
critical_error_processor.py

Lambda function that processes critical error messages from SQS and writes them to DynamoDB.

This function is triggered by the CriticalErrorsQueue and handles the actual database writes,
replacing the direct DynamoDB write that was previously in PyCommon.

Copyright (c) 2025 Vanderbilt University
Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
"""

import json
import os
import time
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pycommon.db_utils import convert_floats_to_decimal
from pycommon.logger import getLogger

logger = getLogger("critical_error_processor")

# DynamoDB setup
dynamodb = boto3.resource("dynamodb")

# Constants
STATUS_ACTIVE = "ACTIVE"
STATUS_RESOLVED = "RESOLVED"
STATUS_RETURNED = "RETURNED"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

# Cap for tracking individual user occurrence counts
MAX_TRACKED_USERS = 15  # Track detailed counts for first 15 users
# After 15, just track unique_user_count without individual details


def generate_error_fingerprint(
    service_name: str,
    function_name: str,
    error_type: str,
    error_message: str
) -> str:
    """
    Generate a unique fingerprint for an error based on its key attributes.
    
    This allows us to detect duplicate errors and group them together.
    We use the first 200 chars of error_message to balance uniqueness vs grouping.
    
    Args:
        service_name: Service where error occurred
        function_name: Function where error occurred
        error_type: Type/class of error
        error_message: Error message (truncated to 200 chars)
    
    Returns:
        str: SHA256 hash of the error fingerprint
    
    Example:
        >>> generate_error_fingerprint(
        ...     "amplify-lambda-admin",
        ...     "get_user",
        ...     "DatabaseConnectionError",
        ...     "Connection timeout after 30 seconds"
        ... )
        "a3f5d8c2e1b4..."
    """
    # Normalize inputs
    service = service_name.lower().strip()
    function = function_name.lower().strip()
    error_type_normalized = error_type.strip()
    
    # Use first 200 chars of error message to group similar errors
    # This prevents minor differences (like timestamps) from creating separate entries
    message_normalized = error_message[:200].strip()
    
    # Create fingerprint string
    fingerprint_str = f"{service}::{function}::{error_type_normalized}::{message_normalized}"
    
    # Hash it for consistent length and privacy
    fingerprint_hash = hashlib.sha256(fingerprint_str.encode()).hexdigest()
    
    logger.debug("Generated fingerprint %s for %s.%s", fingerprint_hash[:16], service, function)
    
    return fingerprint_hash


def find_existing_error_by_fingerprint(
    table,
    fingerprint: str
) -> Optional[Dict[str, Any]]:
    """
    Check if an error with this fingerprint already exists.
    
    Queries the error-fingerprint-index GSI to find existing errors.
    
    Args:
        table: DynamoDB table resource
        fingerprint: SHA256 hash of error fingerprint
    
    Returns:
        dict: Existing error item if found, None otherwise
    """
    try:
        response = table.query(
            IndexName="error-fingerprint-index",
            KeyConditionExpression=Key("error_fingerprint").eq(fingerprint),
            Limit=1,
            ScanIndexForward=False  # Get most recent first
        )
        
        items = response.get("Items", [])
        if items:
            logger.debug("Found existing error with fingerprint %s", fingerprint[:16])
            return items[0]
        
        return None
    
    except Exception as e:
        logger.error("Error querying by fingerprint: %s", str(e))
        return None


def write_critical_error_to_dynamo(
    table,
    service_name: str,
    function_name: str,
    error_type: str,
    error_message: str,
    current_user: str = None,
    severity: str = SEVERITY_CRITICAL,
    stack_trace: str = None,
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Write a critical error to DynamoDB with smart deduplication.
    
    If an error with the same fingerprint already exists:
    - If ACTIVE: Increment occurrence_count
    - If RESOLVED: Change status to RETURNED, move resolution to history, reset count
    - If RETURNED: Increment occurrence_count
    
    If no existing error: Create new entry
    
    Args:
        table: DynamoDB table resource
        service_name: Name of the service where error occurred
        function_name: Specific function/handler name
        error_type: Classification of the error
        error_message: Detailed error message
        current_user: Username/email of the user who triggered the error
        severity: Error severity level
        stack_trace: Full stack trace for debugging
        context: Additional metadata
    
    Returns:
        dict: Success status and error_id
    """
    try:
        # Generate error fingerprint for deduplication
        fingerprint = generate_error_fingerprint(
            service_name, function_name, error_type, error_message
        )
        
        # Check if this error already exists
        existing_error = find_existing_error_by_fingerprint(table, fingerprint)
        
        current_timestamp = int(time.time())
        current_user_normalized = current_user if current_user else "system"
        
        if existing_error:
            # Error exists - update it instead of creating duplicate
            error_id = existing_error["error_id"]
            existing_status = existing_error.get("status", STATUS_ACTIVE)
            occurrence_count = existing_error.get("occurrence_count", 1)
            
            # affected_users is now a dict: {user: count}
            affected_users_dict = existing_error.get("affected_users", {})
            if isinstance(affected_users_dict, list):
                # Migrate old list format to dict format
                affected_users_dict = {user: 1 for user in affected_users_dict if user != "__MANY_USERS__"}
            
            # Track unique user count separately (total across all time)
            unique_user_count = existing_error.get("unique_user_count", len(affected_users_dict))
            
            # Update user occurrence counts
            if len(affected_users_dict) < MAX_TRACKED_USERS:
                # Still tracking individual users - increment their count
                affected_users_dict[current_user_normalized] = affected_users_dict.get(current_user_normalized, 0) + 1
            else:
                # Hit cap - only increment if this user is already tracked
                if current_user_normalized in affected_users_dict:
                    affected_users_dict[current_user_normalized] += 1
                else:
                    # New user but we're at cap - just increment unique count
                    unique_user_count += 1
            
            logger.info(
                "Duplicate error detected (fingerprint: %s), updating existing error_id: %s",
                fingerprint[:16],
                error_id
            )
            
            if existing_status == STATUS_RESOLVED:
                # ERROR IS BACK! Move resolution to history
                logger.warning(
                    "Resolved error has RETURNED: %s | %s.%s",
                    error_id,
                    service_name,
                    function_name
                )
                
                # Build resolution history entry
                resolution_history = existing_error.get("resolution_history", [])
                resolution_history.append({
                    "resolved_at": existing_error.get("resolved_at"),
                    "resolved_by": existing_error.get("resolved_by"),
                    "resolution_notes": existing_error.get("resolution_notes"),
                    "occurrences_before_return": occurrence_count
                })
                
                # Update to RETURNED status and reset count (keep affected_users dict)
                table.update_item(
                    Key={"error_id": error_id},
                    UpdateExpression="""
                        SET #status = :returned,
                            last_occurrence = :now,
                            occurrence_count = :one,
                            affected_users = :users,
                            unique_user_count = :user_count,
                            current_user = :user,
                            stack_trace = :stack,
                            #context = :ctx,
                            resolution_history = :history
                        REMOVE resolved_at, resolved_by, resolution_notes, #ttl
                    """,
                    ExpressionAttributeNames={
                        "#status": "status",
                        "#context": "context",
                        "#ttl": "ttl"
                    },
                    ExpressionAttributeValues={
                        ":returned": STATUS_RETURNED,
                        ":now": current_timestamp,
                        ":one": 1,
                        ":users": convert_floats_to_decimal(affected_users_dict),
                        ":user_count": unique_user_count,
                        ":user": current_user_normalized,
                        ":stack": stack_trace,
                        ":ctx": convert_floats_to_decimal(context) if context else {},
                        ":history": resolution_history
                    }
                )
                
                return {
                    "success": True,
                    "error_id": error_id,
                    "message": "Error has RETURNED after being resolved",
                    "action": "returned"
                }
            
            else:
                # Error is ACTIVE or RETURNED - just increment count
                new_count = occurrence_count + 1
                
                update_expr = """
                    SET last_occurrence = :now,
                        occurrence_count = :count,
                        affected_users = :users,
                        unique_user_count = :user_count,
                        current_user = :user,
                        stack_trace = :stack
                """
                
                expr_values = {
                    ":now": current_timestamp,
                    ":count": new_count,
                    ":users": convert_floats_to_decimal(affected_users_dict),
                    ":user_count": unique_user_count,
                    ":user": current_user_normalized,
                    ":stack": stack_trace
                }
                
                # Add context if provided
                expr_names = None
                if context:
                    update_expr += ", #context = :ctx"
                    expr_values[":ctx"] = convert_floats_to_decimal(context)
                    expr_names = {"#context": "context"}
                
                update_params = {
                    "Key": {"error_id": error_id},
                    "UpdateExpression": update_expr,
                    "ExpressionAttributeValues": expr_values
                }
                
                if expr_names:
                    update_params["ExpressionAttributeNames"] = expr_names
                
                table.update_item(**update_params)
                
                logger.info(
                    "Incremented occurrence_count to %d for error %s",
                    new_count,
                    error_id
                )
                
                return {
                    "success": True,
                    "error_id": error_id,
                    "message": f"Error occurrence #{new_count} logged",
                    "action": "incremented",
                    "occurrence_count": new_count
                }
        
        else:
            # New error - create fresh entry
            error_id = str(uuid.uuid4())
            current_iso = datetime.now(timezone.utc).isoformat()
            
            item = {
                "error_id": error_id,
                "error_fingerprint": fingerprint,
                "timestamp": current_timestamp,
                "last_occurrence": current_timestamp,
                "occurrence_count": 1,
                "created_at": current_iso,
                "status": STATUS_ACTIVE,
                "severity": severity,
                "service_name": service_name,
                "function_name": function_name,
                "error_type": error_type,
                "error_message": error_message,
                "current_user": current_user_normalized,
                "affected_users": {current_user_normalized: 1},  # Dict: {user: count}
                "unique_user_count": 1,
                "resolution_history": []
            }
            
            if stack_trace:
                item["stack_trace"] = stack_trace
            
            if context:
                item["context"] = convert_floats_to_decimal(context)
            
            table.put_item(Item=item)
            
            logger.info(
                "New critical error created: %s | %s.%s | Type: %s",
                error_id,
                service_name,
                function_name,
                error_type
            )
            
            return {
                "success": True,
                "error_id": error_id,
                "message": "Critical error logged successfully",
                "action": "created"
            }
    
    except ClientError as e:
        error_msg = f"DynamoDB error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "message": error_msg}
    
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "message": error_msg}


def process_critical_error_from_sqs(event: dict, context) -> dict:
    """
    Lambda handler for processing critical error messages from SQS.
    
    Args:
        event: SQS event containing Records with critical error data
        context: Lambda context object
    
    Returns:
        dict: Response with batchItemFailures for partial batch failure handling
    
    Environment Variables:
        CRITICAL_ERRORS_DYNAMODB_TABLE: DynamoDB table name for storing errors
    """
    
    table_name = os.environ.get("CRITICAL_ERRORS_DYNAMODB_TABLE")
    if not table_name:
        logger.error("CRITICAL_ERRORS_DYNAMODB_TABLE environment variable not set")
        # Don't return failures - this is a configuration error
        return {"batchItemFailures": []}
    
    critical_errors_table = dynamodb.Table(table_name)
    batch_item_failures = []
    
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        
        try:
            # Parse the SQS message body
            body = json.loads(record["body"])
            
            # Validate required fields
            required_fields = ["function_name", "error_type", "error_message"]
            missing_fields = [f for f in required_fields if f not in body]
            
            if missing_fields:
                logger.error(
                    "Missing required fields in message %s: %s",
                    message_id,
                    missing_fields
                )
                # Skip this message - don't retry malformed data
                continue
            
            # Write to DynamoDB
            result = write_critical_error_to_dynamo(
                critical_errors_table,
                service_name=body.get("service_name", "unknown"),
                function_name=body["function_name"],
                error_type=body["error_type"],
                error_message=body["error_message"],
                current_user=body.get("current_user"),
                severity=body.get("severity", SEVERITY_CRITICAL),
                stack_trace=body.get("stack_trace"),
                context=body.get("context")
            )
            
            if not result.get("success"):
                logger.error(
                    "Failed to write error to DynamoDB for message %s: %s",
                    message_id,
                    result.get("message")
                )
                # Add to batch failures for retry
                batch_item_failures.append({"itemIdentifier": message_id})
            else:
                logger.info(
                    "Successfully processed critical error: %s (message_id: %s)",
                    result.get("error_id"),
                    message_id
                )
        
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in message %s: %s", message_id, str(e))
            # Don't retry - malformed JSON
            continue
        
        except Exception as e:
            logger.error(
                "Unexpected error processing message %s: %s",
                message_id,
                str(e),
                exc_info=True
            )
            # Add to batch failures for retry
            batch_item_failures.append({"itemIdentifier": message_id})
    
    return {"batchItemFailures": batch_item_failures}
