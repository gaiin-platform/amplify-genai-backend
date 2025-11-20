from scheduled_tasks_events.scheduled_tasks_registry import (
    create_scheduled_task,
    get_scheduled_task,
    list_scheduled_tasks,
    update_scheduled_task,
    delete_scheduled_task,
    get_task_execution_details,
    execute_specific_task,
)
from pycommon.api.ops import api_tool


@api_tool(
    path="/vu-agent/create-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="createScheduledTask",
    description="Create a new scheduled task.",
    parameters={
        "type": "object",
        "properties": {
            "taskName": {"type": "string", "description": "Name of the task"},
            "description": {"type": "string", "description": "Description of the task"},
            "taskInstructions": {
                "type": "string",
                "description": "Instructions for the task execution",
            },
            "taskType": {
                "type": "string",
                "description": "Type of task ('actionSet', 'assistant', 'apiTool')",
            },
            "objectInfo": {
                "type": "object",
                "properties": {
                    "objectId": {"type": "string"},
                    "objectName": {"type": "string"},
                    "data" : {"type": "object"}
                },
                "required": ["objectId", "objectName"],
                "description": "Information about the associated object",
            },
            "cronExpression": {
                "type": "string",
                "description": "Cron expression for scheduling",
            },
            "dateRange": {
                "type": "object",
                "properties": {
                    "startDate": {"type": ["string", "null"]},
                    "endDate": {"type": ["string", "null"]},
                },
                "description": "Start and end date range (optional)",
            },
            "active": {
                "type": "boolean",
                "description": "Whether the task is active (optional)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tags (optional)",
            },
            "notifyOnCompletion": {
                "type": "boolean",
                "description": "Whether to notify on completion (optional)",
            },
            "notifyOnFailure": {
                "type": "boolean",
                "description": "Whether to notify on failure (optional)",
            },
            "notifyEmailAddresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Email addresses to notify (optional)",
            },
            "timeZone": {"type": "string", "description": "Time zone for scheduling"},
        },
        "required": [
            "taskName",
            "description",
            "taskInstructions",
            "taskType",
            "objectInfo",
            "cronExpression",
        ],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the task was created successfully",
            },
            "taskId": {
                "type": "string",
                "description": "The ID of the created task when successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def create_scheduled_task_handler(
    current_user,
    access_token,
    account_id,
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
    time_zone=None,
):
    try:
        print(f"Creating scheduled task with object_info: {object_info}")
        task_id = create_scheduled_task(
            current_user=current_user,
            task_name=task_name,
            description=description,
            task_instructions=task_instructions,
            task_type=task_type,
            object_info=object_info,
            cron_expression=cron_expression,
            date_range=date_range,
            active=active,
            tags=tags,
            notify_on_completion=notify_on_completion,
            notify_on_failure=notify_on_failure,
            notify_email_addresses=notify_email_addresses,
            access_token=access_token,
            account=account_id,
            time_zone=time_zone,
        )
        return {
            "success": True,
            "taskId": task_id,
            "message": "Scheduled task created successfully",
        }
    except Exception as e:
        print(f"Error creating scheduled task: {e}")
        return {
            "success": False,
            "message": f"Failed to create scheduled task: {str(e)}",
        }


@api_tool(
    path="/vu-agent/get-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="getScheduledTask",
    description="Get a scheduled task by ID.",
    parameters={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "ID of the task to retrieve"}
        },
        "required": ["taskId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the task was retrieved successfully",
            },
            "task": {
                "type": "object",
                "properties": {
                    "taskId": {"type": "string"},
                    "taskName": {"type": "string"},
                    "description": {"type": "string"},
                    "taskInstructions": {"type": "string"},
                    "taskType": {"type": "string"},
                    "objectInfo": {"type": "object"},
                    "cronExpression": {"type": "string"},
                    "active": {"type": "boolean"},
                    "logs": {"type": "array"},
                    "dateRange": {"type": "object"},
                    "tags": {"type": "array"},
                    "notifyOnCompletion": {"type": "boolean"},
                    "notifyOnFailure": {"type": "boolean"},
                    "notifyEmailAddresses": {"type": "array"},
                },
                "description": "Task details when found",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def get_scheduled_task_handler(current_user, access_token, task_id):
    try:
        task = get_scheduled_task(current_user, task_id)
        if task is None:
            return {"success": False, "message": "Task not found"}
        return {"success": True, "task": task, "message": "Task retrieved successfully"}
    except Exception as e:
        print(f"Error getting scheduled task: {e}")
        return {"success": False, "message": f"Failed to get scheduled task: {str(e)}"}


@api_tool(
    path="/vu-agent/list-scheduled-tasks",
    tags=["scheduled-tasks", "default"],
    name="listScheduledTasks",
    description="List all scheduled tasks for the current user.",
    parameters={"type": "object", "properties": {}},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tasks were retrieved successfully",
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "taskId": {"type": "string"},
                        "taskName": {"type": "string"},
                        "taskType": {"type": "string"},
                        "active": {"type": "boolean"},
                    },
                },
                "description": "List of tasks with essential fields",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def list_scheduled_tasks_handler(current_user, access_token):
    try:
        tasks = list_scheduled_tasks(current_user)
        return {
            "success": True,
            "tasks": tasks,
            "message": "Tasks retrieved successfully",
        }
    except Exception as e:
        print(f"Error listing scheduled tasks: {e}")
        return {
            "success": False,
            "message": f"Failed to list scheduled tasks: {str(e)}",
        }


@api_tool(
    path="/vu-agent/update-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="updateScheduledTask",
    description="Update an existing scheduled task.",
    parameters={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "ID of the task to update"},
            "taskName": {
                "type": "string",
                "description": "Name of the task (optional)",
            },
            "description": {
                "type": "string",
                "description": "Description of the task (optional)",
            },
            "taskInstructions": {
                "type": "string",
                "description": "Instructions for the task execution (optional)",
            },
            "taskType": {"type": "string", "description": "Type of task (optional)"},
            "objectInfo": {
                "type": "object",
                "properties": {
                    "objectId": {"type": "string"},
                    "objectName": {"type": "string"},
                },
                "required": ["objectId", "objectName"],
                "description": "Information about the associated object (optional)",
            },
            "cronExpression": {
                "type": "string",
                "description": "Cron expression for scheduling (optional)",
            },
            "dateRange": {
                "type": "object",
                "properties": {
                    "startDate": {"type": ["string", "null"]},
                    "endDate": {"type": ["string", "null"]},
                },
                "description": "Start and end date range (optional)",
            },
            "active": {
                "type": "boolean",
                "description": "Whether the task is active (optional)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tags (optional)",
            },
            "notifyOnCompletion": {
                "type": "boolean",
                "description": "Whether to notify on completion (optional)",
            },
            "notifyOnFailure": {
                "type": "boolean",
                "description": "Whether to notify on failure (optional)",
            },
            "notifyEmailAddresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Email addresses to notify (optional)",
            },
            "timeZone": {"type": "string", "description": "Time zone for scheduling"},
        },
        "required": ["taskId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the task was updated successfully",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def update_scheduled_task_handler(
    current_user,
    access_token,
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
    try:
        result = update_scheduled_task(
            current_user=current_user,
            task_id=task_id,
            task_name=task_name,
            description=description,
            task_instructions=task_instructions,
            task_type=task_type,
            object_info=object_info,
            cron_expression=cron_expression,
            date_range=date_range,
            active=active,
            tags=tags,
            notify_on_completion=notify_on_completion,
            notify_on_failure=notify_on_failure,
            notify_email_addresses=notify_email_addresses,
            time_zone=time_zone,
        )
        return result
    except Exception as e:
        print(f"Error updating scheduled task: {e}")
        return {
            "success": False,
            "message": f"Failed to update scheduled task: {str(e)}",
        }


@api_tool(
    path="/vu-agent/delete-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="deleteScheduledTask",
    description="Delete a scheduled task.",
    parameters={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "ID of the task to delete"}
        },
        "required": ["taskId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the task was deleted successfully",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def delete_scheduled_task_handler(current_user, access_token, task_id):
    try:
        result = delete_scheduled_task(current_user, task_id, access_token)
        return result
    except Exception as e:
        print(f"Error deleting scheduled task: {e}")
        return {
            "success": False,
            "message": f"Failed to delete scheduled task: {str(e)}",
        }


@api_tool(
    path="/vu-agent/get-task-execution-details",
    tags=["scheduled-tasks", "default"],
    name="getTaskExecutionDetails",
    description="Get the detailed logs for a specific task execution.",
    parameters={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "ID of the task"},
            "executionId": {
                "type": "string",
                "description": "ID of the execution to get details for",
            },
        },
        "required": ["taskId", "executionId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the execution details were retrieved successfully",
            },
            "details": {
                "type": "object",
                "properties": {
                    "executionId": {
                        "type": "string",
                        "description": "Unique identifier for this execution",
                    },
                    "source": {
                        "type": "string",
                        "description": "Source of the execution (e.g., 'scheduled-task', 'manual-task-run')",
                    },
                    "executedAt": {
                        "type": "string",
                        "description": "ISO timestamp when the execution occurred",
                    },
                    "detailsKey": {
                        "type": "string",
                        "description": "S3 key where detailed execution logs are stored",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["running", "success", "failure"],
                        "description": "Status of the execution",
                    },
                    "details": {
                        "type": "object",
                        "properties": {
                            "result": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string"},
                                        "content": {},
                                    },
                                },
                                "description": "Conversation history and results when status is success",
                            },
                            "completedAt": {
                                "type": "string",
                                "description": "ISO timestamp when execution completed successfully",
                            },
                            "sessionId": {
                                "type": "string",
                                "description": "Session identifier when status is running",
                            },
                            "startTime": {
                                "type": "string",
                                "description": "ISO timestamp when execution started",
                            },
                            "message": {
                                "type": "string",
                                "description": "Error message when status is failure",
                            },
                            "error": {
                                "type": "string",
                                "description": "Error details when status is failure",
                            },
                            "failedAt": {
                                "type": "string",
                                "description": "ISO timestamp when execution failed",
                            },
                            "source": {
                                "type": "string",
                                "description": "Source of the execution",
                            },
                        },
                        "description": "Detailed execution information retrieved from S3",
                    },
                    "detailsError": {
                        "type": "string",
                        "description": "Error message if details could not be retrieved from S3",
                    },
                },
                "description": "Complete execution details with merged S3 data",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def get_task_execution_details_handler(
    current_user, access_token, task_id, execution_id
):
    try:
        details = get_task_execution_details(current_user, task_id, execution_id)
        if details is None:
            return {"success": False, "message": "Execution record not found"}
        return {
            "success": True,
            "details": details,
            "message": "Execution details retrieved successfully",
        }
    except Exception as e:
        print(f"Error getting task execution details: {e}")
        return {
            "success": False,
            "message": f"Failed to get task execution details: {str(e)}",
        }


@api_tool(
    path="/vu-agent/execute-task",
    tags=["scheduled-tasks", "default"],
    name="executeTask",
    description="Manually execute a specific scheduled task immediately.",
    parameters={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "ID of the task to execute"}
        },
        "required": ["taskId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the task was queued for execution successfully",
            },
            "message": {"type": "string", "description": "Success or error message"},
            "taskId": {
                "type": "string",
                "description": "The ID of the task that was queued when successful",
            },
        },
        "required": ["success", "message"],
    },
)
def execute_task_handler(current_user, access_token, task_id):
    try:
        result = execute_specific_task(current_user, task_id)
        return result
    except Exception as e:
        print(f"Error executing task: {e}")
        return {"success": False, "message": f"Failed to execute task: {str(e)}"}
