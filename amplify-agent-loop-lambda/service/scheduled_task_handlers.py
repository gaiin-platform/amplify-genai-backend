from scheduled_tasks_events.scheduled_tasks_registry import (
    create_scheduled_task,
    get_scheduled_task,
    list_scheduled_tasks,
    update_scheduled_task,
    delete_scheduled_task,
    get_task_execution_details,
    execute_specific_task
)
from common.ops import vop

@vop(
    path="/vu-agent/create-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="createScheduledTask",
    description="Create a new scheduled task.",
    params={
        "taskName": "Name of the task",
        "description": "Description of the task",
        "taskInstructions": "Instructions for the task execution",
        "taskType": "Type of task ('actionSet' or 'assistant')",
        "objectInfo": "Information about the associated object",
        "cronExpression": "Cron expression for scheduling",
        "dateRange": "Start and end date range (optional)",
        "active": "Whether the task is active (optional)",
        "tags": "List of tags (optional)",
        "notifyOnCompletion": "Whether to notify on completion (optional)",
        "notifyOnFailure": "Whether to notify on failure (optional)",
        "notifyEmailAddresses": "Email addresses to notify (optional)"
    },
    schema={
        "type": "object",
        "properties": {
            "taskName": {"type": "string"},
            "description": {"type": "string"},
            "taskInstructions": {"type": "string"},
            "taskType": {"type": "string"},
            "objectInfo": {
                "type": "object",
                "properties": {
                    "objectId": {"type": "string"},
                    "objectName": {"type": "string"}
                },
                "required": ["objectId", "objectName"]
            },
            "cronExpression": {"type": "string"},
            "dateRange": {
                "type": "object",
                "properties": {
                    "startDate": {"type": ["string", "null"]},
                    "endDate": {"type": ["string", "null"]}
                }
            },
            "active": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "notifyOnCompletion": {"type": "boolean"},
            "notifyOnFailure": {"type": "boolean"},
            "notifyEmailAddresses": {"type": "array", "items": {"type": "string"}},
            "timeZone": {"type": "string"}
        },
        "required": ["taskName", "description", "taskInstructions", "taskType", "objectInfo", "cronExpression"]
    }
)
def create_scheduled_task_handler(current_user, access_token, account_id, task_name, description, task_instructions, 
                                 task_type, object_info, cron_expression, date_range=None, active=True, 
                                 tags=None, notify_on_completion=False, notify_on_failure=False, 
                                 notify_email_addresses=None, time_zone=None):
    try:
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
            time_zone=time_zone
        )
        return {"success": True, "taskId": task_id, "message": "Scheduled task created successfully"}
    except Exception as e:
        print(f"Error creating scheduled task: {e}")
        return {"success": False, "message": f"Failed to create scheduled task: {str(e)}"}

@vop(
    path="/vu-agent/get-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="getScheduledTask",
    description="Get a scheduled task by ID.",
    params={
        "taskId": "ID of the task to retrieve"
    },
    schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"}
        },
        "required": ["taskId"]
    }
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

@vop(
    path="/vu-agent/list-scheduled-tasks",
    tags=["scheduled-tasks", "default"],
    name="listScheduledTasks",
    description="List all scheduled tasks for the current user.",
    params={},
    schema={
        "type": "object",
        "properties": {}
    }
)
def list_scheduled_tasks_handler(current_user, access_token):
    try:
        tasks = list_scheduled_tasks(current_user)
        return {"success": True, "tasks": tasks, "message": "Tasks retrieved successfully"}
    except Exception as e:
        print(f"Error listing scheduled tasks: {e}")
        return {"success": False, "message": f"Failed to list scheduled tasks: {str(e)}"}

@vop(
    path="/vu-agent/update-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="updateScheduledTask",
    description="Update an existing scheduled task.",
    params={
        "taskId": "ID of the task to update",
        "taskName": "Name of the task (optional)",
        "description": "Description of the task (optional)",
        "taskInstructions": "Instructions for the task execution (optional)",
        "taskType": "Type of task (optional)",
        "object": "Information about the associated object (optional)",
        "cronExpression": "Cron expression for scheduling (optional)",
        "dateRange": "Start and end date range (optional)",
        "active": "Whether the task is active (optional)",
        "tags": "List of tags (optional)",
        "notifyOnCompletion": "Whether to notify on completion (optional)",
        "notifyOnFailure": "Whether to notify on failure (optional)",
        "notifyEmailAddresses": "Email addresses to notify (optional)"
    },
    schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},
            "taskName": {"type": "string"},
            "description": {"type": "string"},
            "taskInstructions": {"type": "string"},
            "taskType": {"type": "string"},
            "objectInfo": {
                "type": "object",
                "properties": {
                    "objectId": {"type": "string"},
                    "objectName": {"type": "string"}
                },
                "required": ["objectId", "objectName"]
            },
            "cronExpression": {"type": "string"},
            "dateRange": {
                "type": "object",
                "properties": {
                    "startDate": {"type": ["string", "null"]},
                    "endDate": {"type": ["string", "null"]}
                }
            },
            "active": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "notifyOnCompletion": {"type": "boolean"},
            "notifyOnFailure": {"type": "boolean"},
            "notifyEmailAddresses": {"type": "array", "items": {"type": "string"}},
            "timeZone": {"type": "string"}
        },
        "required": ["taskId"]
    }
)
def update_scheduled_task_handler(current_user, access_token, task_id, task_name=None, description=None, 
                                 task_instructions=None, task_type=None, object_info=None, 
                                 cron_expression=None, date_range=None, active=None, 
                                 tags=None, notify_on_completion=None, notify_on_failure=None, 
                                 notify_email_addresses=None, time_zone=None):
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
            time_zone=time_zone
        )
        return result
    except Exception as e:
        print(f"Error updating scheduled task: {e}")
        return {"success": False, "message": f"Failed to update scheduled task: {str(e)}"}

@vop(
    path="/vu-agent/delete-scheduled-task",
    tags=["scheduled-tasks", "default"],
    name="deleteScheduledTask",
    description="Delete a scheduled task.",
    params={
        "taskId": "ID of the task to delete"
    },
    schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"}
        },
        "required": ["taskId"]
    }
)
def delete_scheduled_task_handler(current_user, access_token, task_id):
    try:
        result = delete_scheduled_task(current_user, task_id)
        return result
    except Exception as e:
        print(f"Error deleting scheduled task: {e}")
        return {"success": False, "message": f"Failed to delete scheduled task: {str(e)}"}

@vop(
    path="/vu-agent/get-task-execution-details",
    tags=["scheduled-tasks", "default"],
    name="getTaskExecutionDetails",
    description="Get the detailed logs for a specific task execution.",
    params={
        "taskId": "ID of the task",
        "executionId": "ID of the execution to get details for"
    },
    schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},
            "executionId": {"type": "string"}
        },
        "required": ["taskId", "executionId"]
    }
)
def get_task_execution_details_handler(current_user, access_token, task_id, execution_id):
    try:
        details = get_task_execution_details(current_user, task_id, execution_id)
        if details is None:
            return {"success": False, "message": "Execution record not found"}
        return {"success": True, "details": details, "message": "Execution details retrieved successfully"}
    except Exception as e:
        print(f"Error getting task execution details: {e}")
        return {"success": False, "message": f"Failed to get task execution details: {str(e)}"}

@vop(
    path="/vu-agent/execute-task",
    tags=["scheduled-tasks", "default"],
    name="executeTask",
    description="Manually execute a specific scheduled task immediately.",
    params={
        "taskId": "ID of the task to execute"
    },
    schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"}
        },
        "required": ["taskId"]
    }
)
def execute_task_handler(current_user, access_token, task_id):
    try:
        result = execute_specific_task(current_user, task_id)
        return result
    except Exception as e:
        print(f"Error executing task: {e}")
        return {"success": False, "message": f"Failed to execute task: {str(e)}"}
