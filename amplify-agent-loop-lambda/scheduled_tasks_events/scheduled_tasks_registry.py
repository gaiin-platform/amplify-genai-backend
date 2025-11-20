from datetime import datetime
import os
from zoneinfo import ZoneInfo
import boto3
import json
import uuid
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from pycommon.api.api_key import deactivate_key
from delegation.api_keys import create_agent_event_api_key
from scheduled_tasks_events.scheduled_tasks import send_tasks_to_queue


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
        print(f"Error creating scheduled task: {e}")
        raise RuntimeError(f"Failed to create scheduled task: {e}")


def get_scheduled_task(current_user, task_id):
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
            "logs": task.get("logs", []),
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
        print(f"Error getting scheduled task: {e}")
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
        print(f"Error listing scheduled tasks: {e}")
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
        print(f"Error updating scheduled task: {e}")
        raise RuntimeError(f"Failed to update scheduled task: {e}")


def delete_scheduled_task(current_user, task_id, access_token):
    """
    Delete a scheduled task.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task to delete

    Returns:
        dict: Result of the delete operation
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
                "message": "Task not found or you don't have permission to delete it",
            }

        scheduled_task = response["Item"]
        
        # Deserialize the apiKeyId from DynamoDB format
        deserializer = TypeDeserializer()
        api_key_id = deserializer.deserialize(scheduled_task["apiKeyId"])
        
        print(f"Deleting task {task_id} with api key id {api_key_id}")
        deactivate_key(access_token, api_key_id)

        # Delete the task from DynamoDB
        dynamodb.delete_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
        )

        return {"success": True, "message": f"Task {task_id} deleted successfully"}

    except Exception as e:
        print(f"Error deleting scheduled task: {e}")
        raise RuntimeError(f"Failed to delete scheduled task: {e}")


def get_task_execution_details(current_user, task_id, execution_id):
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
    logs_bucket = os.environ.get("SCHEDULED_TASKS_LOGS_BUCKET")

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

        # If the record has details in S3, fetch them
        if "detailsKey" in execution_record:
            try:
                s3_response = s3.get_object(
                    Bucket=logs_bucket, Key=execution_record["detailsKey"]
                )
                details = json.loads(s3_response["Body"].read().decode("utf-8"))
                execution_record["details"] = details
            except Exception as e:
                print(f"Error fetching execution details from S3: {e}")
                execution_record["detailsError"] = str(e)

        return execution_record

    except Exception as e:
        print(f"Error getting execution details: {e}")
        raise RuntimeError(f"Failed to get execution details: {e}")


def execute_specific_task(current_user, task_id):
    print(f"Executing task {task_id} for user {current_user}")
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
        print(f"Retrieving task {task_id} from DynamoDB")
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
        print(f"Error executing task: {e}")
        return {"success": False, "message": f"Failed to execute task: {str(e)}"}
