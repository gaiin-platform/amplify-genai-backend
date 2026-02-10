from datetime import datetime
import os
import boto3
import json
import uuid
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from pycommon.api.api_key import deactivate_key
from pycommon.api.user_data import load_user_data, delete_user_data
from pycommon.lzw import is_lzw_compressed_format, lzw_uncompress
from delegation.api_keys import create_agent_event_api_key
from scheduled_tasks_events.scheduled_tasks import send_tasks_to_queue
from pycommon.logger import getLogger
logger = getLogger("scheduled_task_registry")


def create_scheduled_task(
    current_user,
    task_name,
    description,
    task_instructions,
    task_type,
    object_info,
    cron_expression,
    date_range=None,
    active=True,
    tags=None,
    notify_on_completion=False,
    notify_on_failure=False,
    notify_email_addresses=None,
    access_token=None,
    account=None,
    time_zone=None,
):
    """
    Create a new scheduled task.

    Args:
        current_user (str): User ID creating the task
        task_name (str): Name of the task
        description (str): Description of the task
        task_instructions (str): Instructions for the task execution
        task_type (str): Type of task ('actionSet' or 'assistant')
        object_info (dict): Information about the associated object (contains objectId and objectName)
        cron_expression (str): Cron expression for scheduling
        date_range (dict, optional): Start and end date range
        active (bool, optional): Whether the task is active
        tags (list, optional): List of tags
        notify_on_completion (bool, optional): Whether to notify on completion
        notify_on_failure (bool, optional): Whether to notify on failure
        notify_email_addresses (list, optional): Email addresses to notify

    Returns:
        str: The ID of the created task
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS client
    dynamodb = boto3.client("dynamodb")
    task_name = task_name.strip()

    # Generate a unique UUID for the new task
    task_id = f"{task_name}_{str(uuid.uuid4())}"

    api_key_response = create_agent_event_api_key(
        user=current_user,
        token=access_token,
        agent_event_name=task_name,
        account=account,
        description=f"Scheduled task: {task_name} - {description}",
        purpose="scheduled_task",
    )

    if not api_key_response or not api_key_response.get("success"):
        return {
            "success": False,
            "data": False,
            "message": "Failed to create API key. Event template was not added.",
        }

    # Extract API Key ID
    api_key_id = api_key_response["data"]["id"]

    # Prepare the item to be inserted into the DynamoDB table
    serializer = TypeSerializer()
    item = {
        "user": serializer.serialize(current_user),
        "taskId": serializer.serialize(task_id),
        "taskName": serializer.serialize(task_name),
        "description": serializer.serialize(description),
        "taskInstructions": serializer.serialize(task_instructions),
        "taskType": serializer.serialize(task_type),
        "objectInfo": serializer.serialize(object_info),
        "cronExpression": serializer.serialize(cron_expression),
        "active": {"N": "1" if active else "0"},
        "createdAt": serializer.serialize(datetime.now().isoformat()),
        "logs": serializer.serialize([]),
        "apiKeyId": serializer.serialize(api_key_id),
        "timeZone": serializer.serialize(time_zone),
    }

    # Add optional fields if they exist
    if date_range:
        item["dateRange"] = serializer.serialize(date_range)
    if tags:
        item["tags"] = serializer.serialize(tags)
    if notify_on_completion is not None:
        item["notifyOnCompletion"] = serializer.serialize(notify_on_completion)
    if notify_on_failure is not None:
        item["notifyOnFailure"] = serializer.serialize(notify_on_failure)
    if notify_email_addresses:
        item["notifyEmailAddresses"] = serializer.serialize(notify_email_addresses)

    try:
        # Insert the task into the DynamoDB table
        dynamodb.put_item(TableName=table_name, Item=item)
        return task_id
    except Exception as e:
        logger.error("Error creating scheduled task: %s", e)
        raise RuntimeError(f"Failed to create scheduled task: {e}")


def get_scheduled_task(current_user, task_id, access_token=None):
    """
    Get a scheduled task by ID.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task to retrieve

    Returns:
        dict: The scheduled task details or None if not found
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS client
    dynamodb = boto3.client("dynamodb")

    try:
        # Lookup the task in DynamoDB
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        # Check if the item exists
        if "Item" not in response:
            return None

        # Deserialize the response item
        deserializer = TypeDeserializer()
        task = {
            key: deserializer.deserialize(value)
            for key, value in response["Item"].items()
        }

        # Return logs array as-is (metadata only)
        # Frontend will call get_task_execution_details() for actual log data
        logs_array = task.get("logs", [])
        
        # Convert to expected format for frontend
        result = {
            "taskId": task["taskId"],
            "taskName": task["taskName"],
            "description": task["description"],
            "taskInstructions": task["taskInstructions"],
            "taskType": task["taskType"],
            "objectInfo": task["objectInfo"],
            "cronExpression": task["cronExpression"],
            "active": task["active"] == 1,
            "logs": logs_array,
        }

        # Add optional fields if they exist
        if "dateRange" in task:
            result["dateRange"] = task["dateRange"]
        if "tags" in task:
            result["tags"] = task["tags"]
        if "notifyOnCompletion" in task:
            result["notifyOnCompletion"] = task["notifyOnCompletion"]
        if "notifyOnFailure" in task:
            result["notifyOnFailure"] = task["notifyOnFailure"]
        if "notifyEmailAddresses" in task:
            result["notifyEmailAddresses"] = task["notifyEmailAddresses"]

        return result

    except Exception as e:
        logger.error("Error getting scheduled task: %s", e)
        raise RuntimeError(f"Failed to get scheduled task: {e}")


def list_scheduled_tasks(current_user):
    """
    List all scheduled tasks for a user with limited fields for efficiency.
    Returns only essential fields: taskId, taskName, type, and active status.

    Args:
        current_user (str): User ID to list tasks for

    Returns:
        list: A list of scheduled tasks with limited fields
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS client
    dynamodb = boto3.client("dynamodb")

    try:
        # Define expression attribute names to avoid reserved keyword issue
        expression_attribute_names = {"#user": "user"}

        tasks = []
        last_evaluated_key = None

        # Loop to handle pagination
        while True:
            # Build query parameters
            query_params = {
                "TableName": table_name,
                "KeyConditionExpression": "#user = :user",
                "ExpressionAttributeNames": expression_attribute_names,
                "ExpressionAttributeValues": {":user": {"S": current_user}},
            }

            # Add pagination token if available
            if last_evaluated_key:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            # Query DynamoDB for tasks by the current user
            response = dynamodb.query(**query_params)

            # Process items from this page
            if "Items" in response and response["Items"]:
                # Deserialize items
                deserializer = TypeDeserializer()

                for item in response["Items"]:
                    task = {
                        key: deserializer.deserialize(value)
                        for key, value in item.items()
                    }

                    # Convert to expected format for frontend - include only essential fields
                    task_data = {
                        "taskId": task["taskId"],
                        "taskName": task["taskName"],
                        "taskType": task["taskType"],
                        "active": task["active"] == 1,
                    }

                    tasks.append(task_data)

            # Check if there are more pages
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        return tasks

    except Exception as e:
        logger.error("Error listing scheduled tasks: %s", e)
        raise RuntimeError(f"Failed to list scheduled tasks: {e}")


def update_scheduled_task(
    current_user,
    task_id,
    task_name=None,
    description=None,
    task_instructions=None,
    task_type=None,
    object_info=None,
    cron_expression=None,
    date_range=None,
    active=None,
    tags=None,
    notify_on_completion=None,
    notify_on_failure=None,
    notify_email_addresses=None,
    time_zone=None,
):
    """
    Update an existing scheduled task.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task to update
        [optional parameters]: Any parameters to update

    Returns:
        dict: Result of the update operation
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS client
    dynamodb = boto3.client("dynamodb")

    try:
        # First, check if the task exists and belongs to the user
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        # If task doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return {
                "success": False,
                "message": "Task not found or you don't have permission to update it",
            }

        # Prepare update expressions
        serializer = TypeSerializer()
        update_expression_parts = []
        expression_attribute_names = {}
        expression_attribute_values = {}

        def add_to_update(field_name, param_value, dynamo_field=None):
            if param_value is not None:
                if dynamo_field is None:
                    dynamo_field = field_name
                placeholder = f":val{len(expression_attribute_values)}"
                update_expression_parts.append(f"#{dynamo_field} = {placeholder}")
                expression_attribute_names[f"#{dynamo_field}"] = dynamo_field
                expression_attribute_values[placeholder] = serializer.serialize(
                    param_value
                )

        # Add each field that needs to be updated
        add_to_update("taskName", task_name)
        add_to_update("description", description)
        add_to_update("taskInstructions", task_instructions)
        add_to_update("taskType", task_type)
        add_to_update("objectInfo", object_info)
        add_to_update("cronExpression", cron_expression)
        add_to_update("dateRange", date_range)
        if active is not None:
            add_to_update("active", 1 if active else 0)
        add_to_update("tags", tags)
        add_to_update("notifyOnCompletion", notify_on_completion)
        add_to_update("notifyOnFailure", notify_on_failure)
        add_to_update("notifyEmailAddresses", notify_email_addresses)
        add_to_update("updatedAt", datetime.now().isoformat())
        add_to_update("timeZone", time_zone)

        # If nothing to update
        if not update_expression_parts:
            return {"success": True, "message": "No changes to update"}

        update_expression = "SET " + ", ".join(update_expression_parts)

        # Update the item in DynamoDB
        dynamodb.update_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
        )

        return {"success": True, "message": f"Task {task_id} updated successfully"}

    except Exception as e:
        logger.error("Error updating scheduled task: %s", e)
        raise RuntimeError(f"Failed to update scheduled task: {e}")


def delete_scheduled_task(current_user, task_id, access_token):
    """
    Delete a scheduled task and archive its logs to consolidation bucket.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task to delete
        access_token (str): Access token for authentication

    Returns:
        dict: Result of the delete operation
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
    logs_bucket = os.environ.get("SCHEDULED_TASKS_LOGS_BUCKET")  #Marked for future deletion
    consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    try:
        # First, check if the task exists and belongs to the user
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        # If task doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return {
                "success": False,
                "message": "Task not found or you don't have permission to delete it",
            }

        scheduled_task = response["Item"]
        
        # Deserialize the task to access logs
        deserializer = TypeDeserializer()
        task_data = {key: deserializer.deserialize(value) for key, value in scheduled_task.items()}
        
        api_key_id = task_data["apiKeyId"]
        logs = task_data.get("logs", [])
        
        logger.info("Deleting task %s with api key id %s", task_id, api_key_id)
        
        # Archive logs instead of deleting them
        if logs:
            legacy_logs_archived = 0
            migrated_logs_archived = 0
            
            # Archive legacy logs (with detailsKey) to consolidation bucket
            for log_entry in logs:
                if "detailsKey" in log_entry:
                    execution_id = log_entry.get("executionId", f"unknown-{legacy_logs_archived}")
                    
                    # Archive legacy S3 log files to consolidation bucket
                    if logs_bucket and consolidation_bucket:
                        try:
                            # Download from legacy bucket
                            s3_response = s3.get_object(
                                Bucket=logs_bucket,
                                Key=log_entry["detailsKey"]
                            )
                            log_content = s3_response["Body"].read()
                            
                            # Archive to consolidation bucket
                            archive_key = f"scheduledTaskLogs/{current_user}/{task_id}/{execution_id}.json"
                            s3.put_object(
                                Bucket=consolidation_bucket,
                                Key=archive_key,
                                Body=log_content,
                                ContentType="application/json"
                            )
                            
                            # Delete original file after successful archival
                            try:
                                s3.delete_object(
                                    Bucket=logs_bucket,
                                    Key=log_entry["detailsKey"]
                                )
                                legacy_logs_archived += 1
                                logger.debug("Archived and deleted legacy log file: %s -> %s", log_entry['detailsKey'], archive_key)
                            except Exception as delete_e:
                                legacy_logs_archived += 1  # Still count as archived
                                logger.warning("Archived legacy log file but failed to delete original %s: %s", log_entry['detailsKey'], delete_e)
                            
                        except Exception as e:
                            logger.warning("Could not archive legacy S3 log file %s: %s", log_entry['detailsKey'], e)
            
            # Archive migrated logs from USER_STORAGE_TABLE to consolidation bucket
            if any(log_entry for log_entry in logs if "detailsKey" not in log_entry):
                try:
                    app_id = "amplify-agent-logs"
                    existing_logs_data = load_user_data(access_token, app_id, "scheduled-task-logs", task_id)
                    
                    if existing_logs_data and "logs" in existing_logs_data:
                        logs_dict = existing_logs_data["logs"]
                        
                        # Archive each log to consolidation bucket
                        for execution_id, compressed_data in logs_dict.items():
                            try:
                                archive_key = f"scheduledTaskLogs/{current_user}/{task_id}/{execution_id}.json"
                                
                                # Decompress data for storage in consolidation bucket
                                if hasattr(compressed_data, '__iter__') and not isinstance(compressed_data, (str, bytes)):
                                    # Already decompressed data
                                    log_data = compressed_data
                                else:
                                    # Decompress LZW data
                                    if is_lzw_compressed_format(compressed_data):
                                        log_data = lzw_uncompress(compressed_data)
                                    else:
                                        log_data = compressed_data
                                
                                # Store in consolidation bucket
                                s3.put_object(
                                    Bucket=consolidation_bucket,
                                    Key=archive_key,
                                    Body=json.dumps(log_data),
                                    ContentType="application/json"
                                )
                                
                                migrated_logs_archived += 1
                                logger.debug("Archived migrated log: %s", archive_key)
                                
                            except Exception as e:
                                logger.warning("Failed to archive migrated log execution %s: %s", execution_id, e)
                    
                    # Delete from USER_STORAGE_TABLE after archival
                    try:
                        delete_user_data(access_token, app_id, "scheduled-task-logs", task_id)
                        logger.debug("Deleted migrated logs for task %s from USER_STORAGE_TABLE after archival", task_id)
                    except Exception as e:
                        logger.warning("Could not delete migrated logs from USER_STORAGE_TABLE for task %s: %s", task_id, e)
                        
                except Exception as e:
                    logger.warning("Could not archive migrated logs for task %s: %s", task_id, e)
            
            logger.info("Log archival completed: %s legacy logs archived, %s migrated logs archived", 
                       legacy_logs_archived, migrated_logs_archived)
            
            if consolidation_bucket and (legacy_logs_archived > 0 or migrated_logs_archived > 0):
                logger.info("Logs archived to consolidation bucket at path: scheduledTaskLogs/%s/%s/", current_user, task_id)
        else:
            logger.debug("No logs to archive for task %s", task_id)
        
        # Deactivate the API key
        deactivate_key(access_token, api_key_id)

        # Delete the task from DynamoDB
        dynamodb.delete_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        return {"success": True, "message": f"Task {task_id} deleted successfully and logs archived"}

    except Exception as e:
        logger.error("Error deleting scheduled task: %s", e)
        raise RuntimeError(f"Failed to delete scheduled task: {e}")


def get_task_execution_details(current_user, task_id, execution_id, access_token=None):
    """
    Get the detailed logs for a specific task execution.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task
        execution_id (str): ID of the execution to get details for

    Returns:
        dict: The execution details or None if not found
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
    logs_bucket = os.environ.get("SCHEDULED_TASKS_LOGS_BUCKET")  #Marked for future deletion

    if not table_name or not logs_bucket:
        raise ValueError(
            "Environment variables 'SCHEDULED_TASKS_TABLE' and 'SCHEDULED_TASKS_LOGS_BUCKET' must be set."
        )

    # Initialize AWS clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    try:
        # First, get the task to find the execution record
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        # If task doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return None

        # Deserialize the task
        deserializer = TypeDeserializer()
        task = {
            key: deserializer.deserialize(value)
            for key, value in response["Item"].items()
        }

        # Find the execution record
        logs = task.get("logs", [])
        execution_record = next(
            (log for log in logs if log.get("executionId") == execution_id), None
        )

        if not execution_record:
            return None

        # Check if legacy (has detailsKey) or migrated (no detailsKey)
        if "detailsKey" in execution_record:
            # Legacy execution: Fetch details from S3
            try:
                s3_response = s3.get_object(
                    Bucket=logs_bucket, Key=execution_record["detailsKey"]
                )
                details = json.loads(s3_response["Body"].read().decode("utf-8"))
                execution_record["details"] = details
            except Exception as e:
                logger.error("Error fetching execution details from S3: %s", e)
                execution_record["detailsError"] = str(e)
        else:
            # Migrated execution: Check USER_STORAGE_TABLE first, then consolidation bucket archive
            details_found = False
            
            if access_token:
                try:
                    app_id = "amplify-agent-logs"
                    consolidated_logs_data = load_user_data(access_token, app_id, "scheduled-task-logs", task_id)
                    
                    # Follow USER_STORAGE_TABLE structure: data.logs[executionId]
                    if consolidated_logs_data:
                        # Try data.logs first (proper structure), then fallback to direct logs
                        if "data" in consolidated_logs_data and "logs" in consolidated_logs_data["data"]:
                            logs_dict = consolidated_logs_data["data"]["logs"]
                        elif "logs" in consolidated_logs_data:
                            logs_dict = consolidated_logs_data["logs"]
                        else:
                            logs_dict = None
                        
                        # DEBUG: Log what we're looking for and what we have
                        if logs_dict is not None:
                            logger.debug("Looking for execution_id: '%s'", execution_id)
                            logger.debug("Available execution_ids in logs_dict: %s", list(logs_dict.keys()))
                            logger.debug("Total logs in dict: %d", len(logs_dict))
                        
                        # Direct lookup by execution_id in USER_STORAGE_TABLE
                        if logs_dict is not None and execution_id in logs_dict:
                            compressed_data = logs_dict[execution_id]
                            
                            # DEBUG: Log the compressed data format
                            logger.debug("Found compressed_data type: %s", type(compressed_data))
                            logger.debug("Compressed_data sample: %s", str(compressed_data)[:200])
                            if isinstance(compressed_data, list):
                                if len(compressed_data) > 0:
                                    logger.debug("First element type: %s, value: %s", type(compressed_data[0]), compressed_data[0])
                            
                            # COMPREHENSIVE DECOMPRESSION LOGIC: Handle multiple data formats
                            try:
                                decompressed_data = None
                                
                                # CASE 1: String representation of LZW array: "[123, 34, 114, ...]"
                                if isinstance(compressed_data, str):
                                    data_stripped = compressed_data.strip()
                                    
                                    # Check for string array format
                                    if data_stripped.startswith('[') and data_stripped.endswith(']'):
                                        logger.debug("Detected string array format, parsing to actual array")
                                        try:
                                            import ast
                                            # Convert string "[123, 34, ...]" to actual array [123, 34, ...]
                                            parsed_array = ast.literal_eval(data_stripped)
                                            if isinstance(parsed_array, list):
                                                logger.debug("Successfully parsed string array, checking LZW compression")
                                                # Now check if this array is LZW compressed
                                                if is_lzw_compressed_format(parsed_array):
                                                    logger.debug("Parsed array is LZW compressed, decompressing")
                                                    decompressed_data = lzw_uncompress(parsed_array)
                                                else:
                                                    logger.debug("Parsed array is not LZW compressed, using as-is")
                                                    decompressed_data = parsed_array
                                            else:
                                                logger.warning("Parsed data is not a list: %s", type(parsed_array))
                                                decompressed_data = compressed_data
                                        except (ValueError, SyntaxError) as e:
                                            logger.warning("Failed to parse string array: %s", e)
                                            decompressed_data = compressed_data
                                    
                                    # Check for Python dict string format: "{'key': 'value', ...}"
                                    elif (data_stripped.startswith("{'") and data_stripped.endswith("'}") and 
                                          "': " in data_stripped):
                                        logger.debug("Detected Python dict string, parsing")
                                        try:
                                            import ast
                                            decompressed_data = ast.literal_eval(data_stripped)
                                            logger.debug("Successfully parsed Python dict string")
                                        except (ValueError, SyntaxError) as e:
                                            logger.warning("DEBUG: Failed to parse Python dict string: %s", e)
                                            decompressed_data = compressed_data
                                    else:
                                        # Regular string, use as-is
                                        logger.debug("Regular string data, using as-is")
                                        decompressed_data = compressed_data
                                
                                # CASE 2: Already a list/array, check LZW compression directly
                                elif isinstance(compressed_data, (list, tuple)):
                                    logger.debug("Data is already array/list, checking LZW compression")
                                    
                                    # CRITICAL FIX: Convert floats to ints (DynamoDB deserializes numbers as floats)
                                    if all(isinstance(x, (int, float)) for x in compressed_data):
                                        logger.debug("Converting float array to integer array for LZW check")
                                        int_array = [int(x) for x in compressed_data]
                                        
                                        if is_lzw_compressed_format(int_array):
                                            logger.debug("Array is LZW compressed, decompressing")
                                            decompressed_data = lzw_uncompress(int_array)
                                        else:
                                            logger.debug("Array is not LZW compressed, using as-is")
                                            decompressed_data = compressed_data
                                    else:
                                        logger.debug("Array contains non-numeric data, using as-is")
                                        decompressed_data = compressed_data
                                
                                # CASE 3: Other data types (dict, etc.), use as-is
                                else:
                                    logger.debug("Data type %s, using as-is", type(compressed_data))
                                    decompressed_data = compressed_data
                                
                                # NORMALIZATION: Re-save data if we converted string array to proper array for consistency
                                should_normalize = False
                                normalized_compressed_data = None
                                
                                if isinstance(compressed_data, str) and isinstance(decompressed_data, (list, dict)):
                                    # We converted a string representation to actual data structure
                                    data_stripped = compressed_data.strip()
                                    if data_stripped.startswith('[') and data_stripped.endswith(']'):
                                        # Convert string array to proper integer array for storage consistency
                                        try:
                                            parsed_array = ast.literal_eval(data_stripped)
                                            if isinstance(parsed_array, list) and all(isinstance(x, (int, float)) for x in parsed_array):
                                                # Normalize to integer array
                                                normalized_compressed_data = [int(x) for x in parsed_array]
                                                should_normalize = True
                                                logger.debug("NORMALIZATION: String array will be converted to integer array for consistency")
                                        except (ValueError, SyntaxError):
                                            pass  # Keep original if parsing fails
                                
                                # Set the final result
                                execution_record["details"] = decompressed_data
                                details_found = True
                                logger.debug("Successfully processed log data for execution %s", execution_id)
                                
                                # Re-save normalized data for consistency going forward
                                if should_normalize and normalized_compressed_data is not None and access_token:
                                    try:
                                        logger.debug("NORMALIZATION: Re-saving normalized data for execution %s", execution_id)
                                        
                                        # Update the logs dictionary with normalized data
                                        if logs_dict is not None:
                                            logs_dict[execution_id] = normalized_compressed_data
                                            
                                            # Save the updated logs dictionary back to USER_STORAGE_TABLE
                                            consolidated_data = {
                                                "taskId": task_id,
                                                "user": current_user,
                                                "logs": logs_dict
                                            }
                                            
                                            from pycommon.api.user_data import save_user_data
                                            save_result = save_user_data(access_token, app_id, "scheduled-task-logs", task_id, consolidated_data)
                                            
                                            if save_result:
                                                logger.debug("NORMALIZATION: Successfully updated logs with normalized data for task %s", task_id)
                                            else:
                                                logger.warning("NORMALIZATION: Failed to save normalized data for task %s", task_id)
                                    except Exception as norm_e:
                                        logger.warning("NORMALIZATION: Failed to normalize data for %s: %s", execution_id, norm_e)
                                
                            except Exception as e:
                                logger.error("Failed to process log data for %s: %s", execution_id, e)
                                execution_record["detailsError"] = f"Failed to process log data: {e}"
                        else:
                            logger.warning("DEBUG: execution_id '%s' not found in logs_dict keys", execution_id)
                                
                except Exception as e:
                    logger.error("Error fetching migrated execution details from USER_STORAGE_TABLE: %s", e)
            
            # If not found in USER_STORAGE_TABLE, check consolidation bucket archive
            if not details_found:
                try:
                    consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
                    if consolidation_bucket:
                        archive_key = f"scheduledTaskLogs/{current_user}/{task_id}/{execution_id}.json"
                        
                        try:
                            s3_response = s3.get_object(
                                Bucket=consolidation_bucket,
                                Key=archive_key
                            )
                            archived_details = json.loads(s3_response["Body"].read().decode("utf-8"))
                            execution_record["details"] = archived_details
                            execution_record["source"] = "archived"  # Indicate this came from archive
                            details_found = True
                            logger.debug("Retrieved execution details from consolidation bucket archive: %s", archive_key)
                            
                        except s3.exceptions.NoSuchKey:
                            logger.debug("Execution %s not found in consolidation bucket archive", execution_id)
                        except Exception as archive_e:
                            logger.warning("Error fetching execution details from consolidation bucket: %s", archive_e)
                    
                    if not details_found:
                        if access_token:
                            execution_record["detailsError"] = f"Execution {execution_id} not found in USER_STORAGE_TABLE or consolidation bucket archive"
                        else:
                            execution_record["detailsError"] = "Access token required for migrated logs and execution not found in consolidation bucket archive"
                        
                except Exception as e:
                    logger.error("Error checking consolidation bucket for archived logs: %s", e)
                    if not details_found:
                        execution_record["detailsError"] = f"Error accessing logs: {str(e)}"
            else:
                execution_record["source"] = "active"  # Indicate this came from active storage

        return execution_record

    except Exception as e:
        logger.error("Error getting execution details: %s", e)
        raise RuntimeError(f"Failed to get execution details: {e}")


def execute_specific_task(current_user, task_id):
    logger.info("Executing task %s for user %s", task_id, current_user)
    """
    Prepare a specific task for immediate execution.
    
    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task to execute
    
    Returns:
        dict: Result of the operation including success status
    """
    # Get environment variables
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")

    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    # Initialize AWS client
    dynamodb = boto3.client("dynamodb")

    try:
        logger.debug("Retrieving task %s from DynamoDB", task_id)
        # First, retrieve the task
        response = dynamodb.get_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        # If task doesn't exist or doesn't belong to the user
        if "Item" not in response:
            return {
                "success": False,
                "message": "Task not found or you don't have permission to execute it",
            }

        # Deserialize the task
        deserializer = TypeDeserializer()
        task = {
            key: deserializer.deserialize(value)
            for key, value in response["Item"].items()
        }

        send_tasks_to_queue([task], "manual-task-run")

        return {
            "success": True,
            "message": f"Task {task_id} has been queued for execution",
            "taskId": task_id,
        }

    except Exception as e:
        logger.error("Error executing task: %s", e)
        return {"success": False, "message": f"Failed to execute task: {str(e)}"}
