import json
import os
from datetime import datetime
import re
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
import uuid
import pytz
from decimal import Decimal

from events.event_handler import MessageHandler
from delegation.api_keys import get_api_key_directly_by_id
from events.event_templates import get_assistant_by_alias
from pycommon.api.user_data import load_user_data
from pycommon.api.ses_email import send_email
from croniter import croniter


class DecimalEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles Decimal objects.
    This is needed because DynamoDB returns Decimal objects for numbers.
    """

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def safe_json_dumps(obj):
    """
    Helper function to safely serialize objects to JSON, handling Decimal types.

    Args:
        obj: The object to serialize

    Returns:
        str: JSON string representation of the object
    """
    return json.dumps(obj, cls=DecimalEncoder)


class TasksMessageHandler(MessageHandler):
    def can_handle(self, message: Dict[str, Any]) -> bool:
        try:
            # Check if this is a scheduled task message
            if message.get("source") == "scheduled-task":
                print(f"TasksMessageHandler can handle this scheduled task message")
                return True
            return False
        except Exception as e:
            print(f"Error in TasksMessageHandler.can_handle: {e}")
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        try:
            # Extract task data
            task_data = message.get("taskData", {})

            # Sanitize task_data to handle Decimal values
            # This ensures we can safely serialize it later
            # We do this by round-tripping through JSON
            task_data = json.loads(safe_json_dumps(task_data))

            user_id = task_data.get("user")
            task_id = task_data.get("taskId")
            task_type = task_data.get("taskType")
            task_instructions = task_data.get("taskInstructions")
            api_key = task_data.get("apiKey")
            source = task_data.get("source")

            runtime = get_timestamp(False)
            last_run_at_utc = datetime.now(pytz.utc).isoformat()

            print(
                f"Processing scheduled task: { task_data.get('taskName')} ({task_id}) for user {user_id}"
            )
            # Clear the lastCheckedAt field so the task can be retried
            if source == "scheduled-task":
                self._reset_task_check_status(user_id, task_id, last_run_at_utc)
            # Create a unique session ID
            session_id = f"scheduled-task-{task_id}-{runtime.strftime('%Y%m%d%H%M%S')}"

            # Format the task instructions as prompt
            prompt = [{"role": "user", "content": task_instructions}]

            # Create agent event payload
            event_payload = {
                "currentUser": user_id,
                "sessionId": session_id,
                "prompt": prompt,
                "metadata": {
                    "accessToken": task_data.get("apiKey", None),
                    "source": "scheduled-task",
                    "eventId": session_id,
                    "timestamp": runtime.isoformat(),
                    "requestContent": {**task_data},
                    "files": [],
                    **self._resolved_object_info(
                        user_id, task_data.get("objectInfo", {}), task_type, api_key
                    ),
                },
            }

            # Record that the task has started execution
            add_task_execution_record(
                user_id,
                task_id,
                "running",
                {
                    "sessionId": session_id,
                    "startTime": get_timestamp(),
                    "source": source,
                },
                execution_id=session_id,
            )

            return event_payload

        except Exception as e:
            print(f"Error processing scheduled task: {e}")
            return None

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        """
        Handle task failure. This is called by the agent_queue when processing fails.

        Args:
            event: The input event that failed processing
            error: The exception that caused the failure
        """
        print(f"Task failure handler for event {event}")
        # Extract task info from event metadata
        task_data = event.get("taskData", {})
        print(f"Task data: {task_data}")
        user_id = task_data.get("user")
        task_id = task_data.get("taskId")

        # Try to extract sessionId - it might be available in task_data or we can reconstruct it
        session_id = task_data.get("sessionId")
        if not session_id and task_id:
            # Try to reconstruct sessionId using current time (best effort)
            runtime = get_timestamp(False)
            session_id = f"scheduled-task-{task_id}-{runtime.strftime('%Y%m%d%H%M%S')}"
        
        # Add sessionId to task_data for consistency
        if session_id:
            task_data["sessionId"] = session_id

        if not user_id or not task_id:
            print(f"Error in onFailure: Missing task data in event")
            add_task_execution_record(
                user_id,
                task_id,
                "failure",
                {
                    "error": error,
                    "message": "Agent failed prior to execution",
                    "failedAt": get_timestamp(),
                    "source": task_data.get("source", "unknown"),
                },
                execution_id=session_id,
            )
            return

        print(f"Task failure handler called for task {task_id}: {str(error)}")
        task_failed(user_id, task_id, str(error), task_data)

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        """
        Handle task success. This is called by the agent_queue when processing completes successfully.

        Args:
            agent_input_event: The input event that was processed
            agent_result: The result of processing
        """
        print(f"Task success handler called for agent input {agent_input_event}")
        # Extract task info from event metadata
        task_data = agent_input_event.get("metadata", {}).get("requestContent", {})
        user_id = agent_input_event.get("currentUser")
        task_id = task_data.get("taskId")
        
        # Get sessionId from the agent_input_event - it should be available at the top level
        session_id = agent_input_event.get("sessionId")
        
        # Add sessionId to task_data if not already there
        if session_id and "sessionId" not in task_data:
            task_data["sessionId"] = session_id

        if not user_id or not task_id:
            print(
                f"Error in onSuccess: Missing task data in event: {agent_input_event}"
            )
            return

        print(f"Task success handler called for task {task_id}")
        task_completed(user_id, task_id, task_data, agent_result)

    def _reset_task_check_status(self, user_id, task_id, last_run_at):
        """Reset the lastCheckedAt field to allow the task to be retried."""
        try:
            table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
            if not table_name:
                raise ValueError(
                    "Environment variable 'SCHEDULED_TASKS_TABLE' must be set."
                )

            dynamodb = boto3.client("dynamodb")

            # Update the task to set lastRunAt and clear the lastCheckedAt field
            dynamodb.update_item(
                TableName=table_name,
                Key={"user": {"S": user_id}, "taskId": {"S": task_id}},
                UpdateExpression="SET lastRunAt = :lra REMOVE lastCheckedAt, lastCheckRunId",
                ExpressionAttributeValues={":lra": {"S": last_run_at}},
                ReturnValues="NONE",
            )

            print(f"Reset check status and updated lastRunAt for task {task_id}")
        except Exception as e:
            print(f"Error resetting task check status: {e}")

    def _resolved_object_info(self, user, object_info, object_type, access_token):
        object_id = object_info.get("objectId")
        if not object_id:
            return {}

        if object_type == "assistant":
            assistant_response = get_assistant_by_alias(user, object_id)
            if not assistant_response["success"]:
                raise Exception(
                    f"The scheduled taskexists, but its associated assistant could not be found."
                )
            return {"assistant": assistant_response["data"]}

        elif object_type == "actionSet":
            print(f"Processing actionSet with object_id: {object_id}")
            APP_ID = "amplify-action-sets"
            ENTITY_TYPE = "action-sets"
            action_set = load_user_data(access_token, APP_ID, ENTITY_TYPE, object_id)
            actions = action_set.get("data", {}).get("actions", [])
            print(f"Found {len(actions)} actions in the action set")
            operations = []

            for action in actions:
                # Extract the operation details
                operation = action.get("operation", {})
                defined_tool_parameters = action.get("parameters", {})

                print(f"Defined tool parameters: {defined_tool_parameters}")

                bindings = {
                    k: v for k, v in defined_tool_parameters.items() if v["value"] != ""
                }

                operation["bindings"] = bindings
                print("Operation: ", operation)
                operations.append(operation)

            return {"operations": operations}
        elif object_type == "apiTool":
            # object_info.data.op should be in the expected operation format
            print(f"Processing apiTool with object_info: {object_info}")
            return {"operations": [object_info.get("data", {}).get("op", {})]}


def task_completed(user_id, task_id, task_data, result):
    """
    Callback function for when a task completes successfully.
    This updates the task record to mark it as complete and calculates the next run time.

    Args:
        user_id (str): The user ID that owns the task
        task_id (str): The task ID that was executed
        result (dict): The result data from the execution
    """
    try:
        source = task_data.get("source")
        # Get sessionId from task_data to use as execution_id
        session_id = task_data.get("sessionId")
        
        # Add task execution record
        add_task_execution_record(
            user_id,
            task_id,
            "success",
            {
                "result": result,
                "completedAt": get_timestamp(),
                "source": task_data.get("source"),
            },
            execution_id=session_id,
        )

        if source == "scheduled-task":
            table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
            if not table_name:
                raise ValueError(
                    "Environment variable 'SCHEDULED_TASKS_TABLE' must be set."
                )

            dynamodb = boto3.client("dynamodb")
            dynamodb.update_item(
                TableName=table_name,
                Key={"user": {"S": user_id}, "taskId": {"S": task_id}},
                UpdateExpression="REMOVE lastCheckedAt, lastCheckRunId",
                ReturnValues="NONE",
            )

            print(f"Task {task_id} completed successfully and reset for next run")

        if task_data.get("notifyOnCompletion", False) and task_data.get(
            "notifyEmailAddresses", []
        ):
            print(f"Sending task completion notification for task {task_id}")
            # Prepare rich notification content
            task_name = task_data.get("taskName", "Unknown Task")
            # Create a detailed failure message
            email_body = f"""
    SCHEDULED TASK COMPLETION: {task_name}

    A scheduled task has completed successfully.

   {extract_task_detail_message(task_data)}

    Run Details:
    {result}

    """
            api_key = task_data.get("apiKey")
            email_subject = f"Scheduled Task Completion: {task_name}"
            email_task_details(
                api_key,
                email_subject,
                email_body,
                task_data.get("notifyEmailAddresses"),
            )

    except Exception as e:
        print(f"Error handling task completion: {e}")


def task_failed(user_id, task_id, error, task_data):
    """
    Callback function for when a task fails.
    This updates the task record to mark it as failed, allows it to be retried, and sends a
    notification if configured.

    Args:
        user_id (str): The user ID that owns the task
        task_id (str): The task ID that was executed
        error (str): The error information
        task_data (dict): The task data with configuration details
    """
    try:
        # Get sessionId from task_data to use as execution_id
        session_id = task_data.get("sessionId")

        # Add task execution record
        add_task_execution_record(
            user_id,
            task_id,
            "failure",
            {
                "message": error,
                "error": "Agent failed to execute task",
                "failedAt": get_timestamp(),
                "source": task_data.get("source"),
            },
            execution_id=session_id,
        )

        if task_data.get("source") == "scheduled-task":
            # Clear the lastCheckedAt field to allow the task to be retried
            table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
            if not table_name:
                raise ValueError(
                    "Environment variable 'SCHEDULED_TASKS_TABLE' must be set."
                )

            dynamodb = boto3.client("dynamodb")
            dynamodb.update_item(
                TableName=table_name,
                Key={"user": {"S": user_id}, "taskId": {"S": task_id}},
                UpdateExpression="REMOVE lastCheckedAt, lastCheckRunId",
                ReturnValues="NONE",
            )

        if task_data.get("notifyOnFailure", False) and task_data.get(
            "notifyEmailAddresses", []
        ):
            # Prepare rich notification content
            task_name = task_data.get("taskName", "Unknown Task")
            failure_time_formatted = get_timestamp(False).strftime("%Y-%m-%d %H:%M:%S")
            # Create a detailed failure message
            email_body = f"""
    SCHEDULED TASK FAILURE: {task_name}

    A scheduled task has failed and requires attention.

   {extract_task_detail_message(task_data)}

   FAILURE TIME: {failure_time_formatted}

    ERROR DETAILS:
    {error}
    """
            # Send the failure notification via email using the existing mechanism
            api_key = task_data.get("apiKey")
            email_subject = f"Scheduled Task Failure: {task_name}"
            email_task_details(
                api_key,
                email_subject,
                email_body,
                task_data.get("notifyEmailAddresses"),
            )

    except Exception as e:
        print(f"Error handling task failure: {e}")
        return None


def get_timestamp(isoformat=True):
    date = datetime.now(pytz.utc)
    if isoformat:
        date = date.isoformat()
    return date


def extract_task_detail_message(task_data):
    task_name = task_data.get("taskName", "Unknown Task")
    task_description = task_data.get("description", "No description")

    def camel_to_title_case(camel_case):
        if not camel_case:
            return "Unknown Type"
        # Insert a space before each uppercase letter (except the first one)
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1 \2", camel_case)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s1)
        return s2.title()

    task_label = camel_to_title_case(task_data.get("taskType"))
    # Extract object info if available
    object_info = task_data.get("objectInfo", {})
    object_name = (
        object_info.get("objectName", "Unknown Object")
        if isinstance(object_info, dict)
        else "Unknown Object"
    )

    return f"""TASK DETAILS:
    - Task Name: {task_name}
    - Description: {task_description}
    - {task_label}: {object_name}"""


def email_task_details(api_key, email_subject, email_body, email_addresses):
    print(f"Sending task notification to configured addresses: {email_addresses}")

    for email_address in email_addresses:
        address = email_address.strip()
        result = send_email(api_key, address, email_subject, email_body)
        if not result:
            print(f"Failed to send task notification to {address}")


def add_task_execution_record(current_user, task_id, status, details=None, execution_id=None):
    """
    Add execution record to a task's logs.

    Args:
        current_user (str): User ID owning the task
        task_id (str): ID of the task
        status (str): Status of the execution (success, failure, timeout)
        details (dict, optional): Additional details about the execution
        execution_id (str, optional): Specific execution ID to use. If provided, will update existing record with same ID

    Returns:
        dict: Result of the operation
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

        # Use provided execution_id or create a new one for backward compatibility
        if execution_id is None:
            execution_id = f"execution-{str(uuid.uuid4())}"
        
        executed_at = datetime.now().isoformat()

        execution_record = {
            "executionId": execution_id,
            "executedAt": executed_at,
            "status": status,
            "source": details.get("source", "unknown"),
        }

        # Store details in S3 if provided
        if details:
            s3_key = f"{current_user}/{task_id}/logs/{execution_id}.json"
            s3.put_object(
                Bucket=logs_bucket,
                Key=s3_key,
                Body=json.dumps(details),
                ContentType="application/json",
            )
            execution_record["detailsKey"] = s3_key

        # Update the task in DynamoDB to add the record to logs and update lastRunAt
        deserializer = TypeDeserializer()
        serializer = TypeSerializer()

        # Get current logs
        logs = []
        if "logs" in response["Item"]:
            logs = deserializer.deserialize(response["Item"]["logs"])

        # Find existing record with same execution_id and update it, or insert new record
        existing_record_index = None
        for i, log_entry in enumerate(logs):
            if log_entry.get("executionId") == execution_id:
                existing_record_index = i
                break
        
        if existing_record_index is not None:
            # Update existing record - preserve startTime from original record
            existing_record = logs[existing_record_index]
            if "startTime" in existing_record and status != "running":
                execution_record["startTime"] = existing_record["startTime"]
            logs[existing_record_index] = execution_record
            print(f"Updated existing execution record {execution_id} with status {status}")
        else:
            # Insert new record at the beginning
            logs.insert(0, execution_record)
            print(f"Created new execution record {execution_id} with status {status}")

        # Update the task
        update_expression = "SET logs = :logs, lastRunAt = :executedAt"
        expression_attribute_values = {
            ":logs": serializer.serialize(logs),
            ":executedAt": serializer.serialize(executed_at),
        }

        dynamodb.update_item(
            TableName=table_name,
            Key={"user": {"S": current_user}, "taskId": {"S": task_id}},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
        )

        return {
            "success": True,
            "message": f"Execution record added successfully",
            "executionId": execution_id,
        }

    except Exception as e:
        print(f"Error adding execution record: {e}")
        raise RuntimeError(f"Failed to add execution record: {e}")


def find_tasks_to_execute():
    """
    Find all scheduled tasks that are due to be executed.
    This function is intended to be called by a Lambda triggered by a CloudWatch scheduled event.

    Returns:
        list: List of tasks to execute
    """
    table_name = os.environ.get("SCHEDULED_TASKS_TABLE")
    if not table_name:
        raise ValueError("Environment variable 'SCHEDULED_TASKS_TABLE' must be set.")

    dynamodb = boto3.client("dynamodb")
    # Always work in UTC for storage and comparisons
    now_utc = datetime.now(pytz.utc)
    now_iso = now_utc.isoformat()

    tasks = []
    run_id = str(uuid.uuid4())  # Unique ID for this scheduler run
    deserializer = TypeDeserializer()

    print(f"Scanning for active tasks. Current time (UTC): {now_iso}, Run ID: {run_id}")

    try:
        # Scan for all active tasks. Filtering for "due" tasks will happen in Lambda.
        paginator = dynamodb.get_paginator("scan")
        page_iterator = paginator.paginate(
            TableName=table_name,
            FilterExpression="active = :active_status",
            ExpressionAttributeValues={":active_status": {"N": "1"}},
        )

        for page in page_iterator:
            for item in page.get("Items", []):
                task = {
                    key: deserializer.deserialize(value) for key, value in item.items()
                }
                user = task.get("user")
                task_id = task.get("taskId")
                cron_expression = task.get("cronExpression")

                # Get user's timezone (default to America/Chicago if not specified)
                user_timezone_str = task.get("timeZone", "America/Chicago")
                try:
                    user_tz = ZoneInfo(user_timezone_str)
                except:
                    print(
                        f"Invalid timezone '{user_timezone_str}' for task {task_id}. Using America/Chicago."
                    )
                    user_tz = ZoneInfo("America/Chicago")

                if not user or not task_id:
                    print(f"Skipping item due to missing user or taskId: {item}")
                    continue

                if not cron_expression:
                    print(
                        f"Task {task_id} for user {user} is active but has no cronExpression. Skipping."
                    )
                    continue

                # Convert current time to user's timezone for display
                now_user_tz = now_utc.astimezone(user_tz)
                print(
                    f"\n--- Processing task {task_id} (user: {user}, cron: {cron_expression}, timezone: {user_timezone_str}) ---"
                )
                print(
                    f"Current time in user's timezone: {now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                )

                # 1. Date Range Check (these remain in UTC as they're already stored that way)
                date_range = task.get("dateRange", {})
                skip_task_due_to_date_range = False
                if "startDate" in date_range and date_range["startDate"]:
                    try:
                        start_date_str = date_range["startDate"]
                        if isinstance(start_date_str, str):
                            # Check if it's a date-only string (YYYY-MM-DD format)
                            if len(start_date_str) == 10 and start_date_str.count('-') == 2:
                                # Date-only string - interpret as start of day in user's timezone
                                start_date_naive = datetime.fromisoformat(start_date_str)
                                start_date_user_tz = start_date_naive.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=user_tz)
                                start_date = start_date_user_tz.astimezone(pytz.utc)
                            else:
                                # Full datetime string - ensure it's UTC aware for comparison with now_utc
                                start_date = datetime.fromisoformat(
                                    start_date_str.replace("Z", "+00:00")
                                ).astimezone(pytz.utc)
                            if now_utc < start_date:
                                skip_task_due_to_date_range = True
                                print(
                                    f"Task not started yet. Start date: {start_date.isoformat()}"
                                )
                    except (ValueError, TypeError) as e:
                        print(
                            f"Error parsing start date for task {task_id} (user {user}): {e}. Skipping."
                        )
                        skip_task_due_to_date_range = True

                if (
                    not skip_task_due_to_date_range
                    and "endDate" in date_range
                    and date_range["endDate"]
                ):
                    try:
                        end_date_str = date_range["endDate"]
                        if isinstance(end_date_str, str):
                            # Check if it's a date-only string (YYYY-MM-DD format)
                            if len(end_date_str) == 10 and end_date_str.count('-') == 2:
                                # Date-only string - interpret as end of day in user's timezone
                                end_date_naive = datetime.fromisoformat(end_date_str)
                                end_date_user_tz = end_date_naive.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=user_tz)
                                end_date = end_date_user_tz.astimezone(pytz.utc)
                            else:
                                # Full datetime string - ensure it's UTC aware
                                end_date = datetime.fromisoformat(
                                    end_date_str.replace("Z", "+00:00")
                                ).astimezone(pytz.utc)
                            if now_utc > end_date:
                                skip_task_due_to_date_range = True
                                print(
                                    f"Task has ended. End date: {end_date.isoformat()}"
                                )
                    except (ValueError, TypeError) as e:
                        print(
                            f"Error parsing end date for task {task_id} (user {user}): {e}. Skipping."
                        )
                        skip_task_due_to_date_range = True

                if skip_task_due_to_date_range:
                    print(f"Skipping task {task_id} for user {user} due to date range.")
                    continue

                # 2. Get last checked time to avoid re-processing instances
                last_checked_at_str = task.get("lastCheckedAt")
                last_checked_at = None
                if last_checked_at_str:
                    try:
                        last_checked_at = datetime.fromisoformat(last_checked_at_str)
                        if last_checked_at.tzinfo is None:
                            last_checked_at = pytz.utc.localize(last_checked_at)
                        else:
                            last_checked_at = last_checked_at.astimezone(pytz.utc)
                        print(f"Last checked at (UTC): {last_checked_at.isoformat()}")
                    except ValueError:
                        print(
                            f"Error parsing lastCheckedAt '{last_checked_at_str}'. Treating as None."
                        )
                        last_checked_at = None

                # 3. Determine base datetime for finding all due instances
                base_dt_utc = None

                attr = ["lastRunAt", "createdAt"]
                # Try lastRunAt first, fall back to createdAt
                for date_type in attr:
                    date_str = task.get(date_type)
                    if date_str and date_str != "":
                        try:
                            # Since these are already UTC with timezone info, just parse directly
                            base_dt_utc = datetime.fromisoformat(date_str)
                            print(
                                f"Using {date_type} as base (UTC): {base_dt_utc.isoformat()}"
                            )
                            break
                        except ValueError:
                            print(f"Error parsing {date_type} '{date_str}'.")
                            continue

                if not base_dt_utc:
                    print(
                        f"Task {task_id} has no valid lastRunAt or createdAt. Skipping."
                    )
                    continue

                # Convert base time to user's timezone for cron calculation
                base_dt_user_tz = base_dt_utc.astimezone(user_tz)
                print(
                    f"Base time in user timezone: {base_dt_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                )

                # 4. Find ALL instances that are due (between base time and now)
                try:
                    # IMPORTANT: Create croniter with the user's timezone
                    # This interprets the cron expression in the user's local time
                    cron_iter = croniter(cron_expression, base_dt_user_tz)
                    due_instances_utc = []

                    # Collect all instances up to current time
                    while True:
                        # Get next instance in user's timezone
                        next_instance_user_tz = cron_iter.get_next(datetime)
                        # Convert to UTC for comparison
                        next_instance_utc = next_instance_user_tz.astimezone(pytz.utc)

                        if next_instance_utc > now_utc:
                            # Log the next scheduled run for clarity
                            print(
                                f"Next scheduled run: {next_instance_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')} ({next_instance_utc.strftime('%Y-%m-%d %H:%M:%S %Z')})"
                            )
                            break

                        # Only include instances that haven't been checked yet
                        if (
                            last_checked_at is None
                            or next_instance_utc > last_checked_at
                        ):
                            due_instances_utc.append(next_instance_utc)
                            print(
                                f"  Found due instance: {next_instance_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')} ({next_instance_utc.strftime('%Y-%m-%d %H:%M:%S %Z')})"
                            )

                        # Safety limit to prevent infinite loops
                        if len(due_instances_utc) > 1000:
                            print(
                                f"WARNING: Task {task_id} has over 1000 due instances. Limiting to most recent 100."
                            )
                            due_instances_utc = due_instances_utc[-100:]
                            break

                    if not due_instances_utc:
                        print(f"No due instances found for task {task_id}")
                        continue

                    # Get the earliest due instance that hasn't been processed
                    earliest_due_instance_utc = due_instances_utc[0]
                    earliest_due_instance_user_tz = (
                        earliest_due_instance_utc.astimezone(user_tz)
                    )
                    print(
                        f"Will process earliest due instance: {earliest_due_instance_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')} ({earliest_due_instance_utc.isoformat()})"
                    )
                    if len(due_instances_utc) > 1:
                        print(
                            f"  Note: {len(due_instances_utc) - 1} additional instance(s) pending"
                        )

                except Exception as e:
                    print(f"Error with croniter for task {task_id}: {e}. Skipping.")
                    import traceback

                    traceback.print_exc()
                    continue

                # 5. Attempt to claim the earliest due instance atomically
                print(
                    f"Attempting to claim task {task_id} for instance {earliest_due_instance_utc.isoformat()}"
                )
                try:
                    # Use conditional update to prevent race conditions
                    # Only update if lastCheckedAt doesn't exist OR is older than this instance
                    dynamodb.update_item(
                        TableName=table_name,
                        Key={"user": {"S": user}, "taskId": {"S": task_id}},
                        UpdateExpression="SET lastCheckedAt = :current_time_iso, lastCheckRunId = :run_id_val",
                        ConditionExpression="attribute_not_exists(lastCheckedAt) OR lastCheckedAt < :earliest_instance_iso",
                        ExpressionAttributeValues={
                            ":current_time_iso": {"S": now_iso},
                            ":run_id_val": {"S": run_id},
                            ":earliest_instance_iso": {
                                "S": earliest_due_instance_utc.isoformat()
                            },
                        },
                        ReturnValues="NONE",
                    )

                    print(
                        f"✓ Successfully claimed task {task_id} (user {user}) for instance {earliest_due_instance_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                    )
                    tasks.append(task)

                except dynamodb.exceptions.ConditionalCheckFailedException:
                    print(
                        f"✗ Task {task_id} (user {user}) already claimed by another process"
                    )
                    continue
                except Exception as e:
                    print(
                        f"✗ Error updating lastCheckedAt for task {task_id} (user {user}): {e}"
                    )
                    continue

        print(f"\n=== Summary: Found {len(tasks)} tasks to execute in run {run_id} ===")
        return tasks

    except Exception as e:
        import traceback

        print(
            f"General error in find_tasks_to_execute (run {run_id}): {e}\n{traceback.format_exc()}"
        )
        raise RuntimeError(f"Failed to find tasks to execute (run {run_id}): {str(e)}")


def send_tasks_to_queue(tasks: List[Dict[str, Any]], task_source="scheduled-task"):
    """
    Send tasks to the agent queue for execution.

    Args:
        tasks (list): List of tasks to send to the queue

    Returns:
        dict: Result of the operation
    """
    # Get environment variable
    queue_url = os.environ.get("AGENT_QUEUE_URL")

    if not queue_url:
        raise ValueError("Environment variable 'AGENT_QUEUE_URL' must be set.")

    # Initialize AWS client
    sqs = boto3.client("sqs")

    successful = []
    failed = []

    for task in tasks:
        current_user = task["user"]
        print(f"Current user: {current_user}")
        task_id = task["taskId"]

        print("Retrieving api key for task")
        api_key_id = task.get("apiKeyId")
        # Retrieve API key
        api_result = get_api_key_directly_by_id(api_key_id)
        if not api_result["success"]:
            failed.append(
                {
                    "taskId": task_id,
                    "userId": current_user,
                    "error": "Failed to get API key for task",
                    "message": api_result.get("message"),
                    "failedAt": get_timestamp(),
                }
            )

        task["apiKey"] = api_result["apiKey"]
        task["source"] = task_source
        try:
            # Create message payload
            message = {"source": "scheduled-task", "taskData": task}
            print(f"Sending task {task_id} to queue")
            # Send message to queue with our safe JSON serialization helper
            response = sqs.send_message(
                QueueUrl=queue_url, MessageBody=safe_json_dumps(message)
            )

            successful.append(
                {"taskId": task_id, "messageId": response.get("MessageId")}
            )

        except Exception as e:
            print(f"Error sending task {task['taskId']} to queue: {e}")
            failed.append(
                {
                    "taskId": task["taskId"],
                    "userId": current_user,
                    "error": str(e),
                    "failedAt": datetime.now(pytz.utc).isoformat(),
                }
            )
    for failedTask in failed:
        id = failedTask["taskId"]
        print(f"Failed to send task {id} to queue: {failedTask['error']}")
        
        # Create a sessionId for this failed queue operation
        runtime = get_timestamp(False)
        session_id = f"scheduled-task-{id}-{runtime.strftime('%Y%m%d%H%M%S')}"
        
        add_task_execution_record(
            failedTask["userId"],
            id,
            "failure",
            {
                "error": "Failed to send task to queue",
                "message": failedTask["error"],
                "failedAt": failedTask["failedAt"],
                "source": task_source,
            },
            execution_id=session_id,
        )
    return {"successful": successful, "failed": failed}


def execute_scheduled_tasks(event, context):
    """
    Main handler for scheduled tasks execution.
    This function is intended to be called by a CloudWatch scheduled event.

    Args:
        event: Lambda event
        context: Lambda context

    Returns:
        dict: Result of the execution
    """
    try:
        # Find tasks to execute
        tasks = find_tasks_to_execute()

        if not tasks:
            return {
                "statusCode": 200,
                "body": safe_json_dumps(
                    {"message": "No tasks to execute", "tasksCount": 0}
                ),
            }

        # Send tasks to queue
        result = send_tasks_to_queue(tasks)

        return {
            "statusCode": 200,
            "body": safe_json_dumps(
                {
                    "message": f"Scheduled {len(result['successful'])} tasks for execution",
                    "successful": len(result["successful"]),
                    "failed": len(result["failed"]),
                    "details": result,
                }
            ),
        }

    except Exception as e:
        print(f"Error executing scheduled tasks: {e}")
        return {
            "statusCode": 500,
            "body": safe_json_dumps(
                {"message": f"Error executing scheduled tasks: {str(e)}"}
            ),
        }
