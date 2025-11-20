import json
import requests
from typing import Dict, List, Optional, Union
from datetime import datetime
from integrations.oauth import get_ms_graph_session

# Planner tasks revolve around plans (which belong to Microsoft 365 Groups),
# and buckets (a way to categorize tasks). The ETag is crucial for updates/deletes

integration_name = "microsoft_planner"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class PlannerError(Exception):
    """Base exception for Planner operations"""

    pass


class PlanNotFoundError(PlannerError):
    """Raised when a plan cannot be found"""

    pass


class TaskNotFoundError(PlannerError):
    """Raised when a task cannot be found"""

    pass


class BucketNotFoundError(PlannerError):
    """Raised when a bucket cannot be found"""

    pass


class ETagError(PlannerError):
    """Raised when there are ETag-related issues"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "plan" in error_message:
            raise PlanNotFoundError("Plan not found")
        elif "task" in error_message:
            raise TaskNotFoundError("Task not found")
        elif "bucket" in error_message:
            raise BucketNotFoundError("Bucket not found")
        raise PlannerError("Resource not found")

    if response.status_code == 412:  # Precondition Failed (ETag mismatch)
        raise ETagError("ETag mismatch - the item has been modified")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise PlannerError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_plans_in_group(
    current_user: str, group_id: str, access_token: str
) -> List[Dict]:
    """
    Retrieves all Planner plans in a specific Microsoft 365 group.

    Args:
        current_user: User identifier
        group_id: Microsoft 365 Group ID

    Returns:
        List of plan details

    Raises:
        PlannerError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/groups/{group_id}/planner/plans"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        plans = response.json().get("value", [])
        return [format_plan(plan) for plan in plans]

    except requests.RequestException as e:
        raise PlannerError(f"Network error while listing plans: {str(e)}")


def list_buckets_in_plan(
    current_user: str, plan_id: str, access_token: str
) -> List[Dict]:
    """
    Lists all buckets in a plan.

    Args:
        current_user: User identifier
        plan_id: Plan ID

    Returns:
        List of bucket details

    Raises:
        PlanNotFoundError: If plan doesn't exist
        PlannerError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/planner/plans/{plan_id}/buckets"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        buckets = response.json().get("value", [])
        return [format_bucket(bucket) for bucket in buckets]

    except requests.RequestException as e:
        raise PlannerError(f"Network error while listing buckets: {str(e)}")


def list_tasks_in_plan(
    current_user: str,
    plan_id: str,
    include_details: bool = False,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists all tasks in a plan with optional detailed information.

    Args:
        current_user: User identifier
        plan_id: Plan ID
        include_details: Whether to include task details

    Returns:
        List of task details

    Raises:
        PlanNotFoundError: If plan doesn't exist
        PlannerError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/planner/plans/{plan_id}/tasks"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        tasks = response.json().get("value", [])
        formatted_tasks = []

        for task in tasks:
            formatted_task = format_task(task)
            if include_details:
                try:
                    details = get_task_details(current_user, task["id"])
                    formatted_task.update(details)
                except TaskNotFoundError:
                    pass  # Skip details if task was deleted
            formatted_tasks.append(formatted_task)

        return formatted_tasks

    except requests.RequestException as e:
        raise PlannerError(f"Network error while listing tasks: {str(e)}")


def create_task(
    current_user: str,
    plan_id: str,
    bucket_id: str,
    title: str,
    assignments: Optional[Dict] = None,
    due_date: Optional[str] = None,
    priority: Optional[int] = None,
    access_token: str = None,
) -> Dict:
    """
    Creates a new task in Planner.

    Args:
        current_user: User identifier
        plan_id: Plan ID
        bucket_id: Bucket ID
        title: Task title
        assignments: Dict of userId -> assignment details
        due_date: Optional due date in ISO format
        priority: Optional priority (0-10, where 10 is highest)

    Returns:
        Dict containing created task details

    Raises:
        PlanNotFoundError: If plan doesn't exist
        BucketNotFoundError: If bucket doesn't exist
        PlannerError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/planner/tasks"

        # Validate inputs
        if not title:
            raise PlannerError("Task title is required")

        if priority is not None and not (0 <= priority <= 10):
            raise PlannerError("Priority must be between 0 and 10")

        if due_date:
            try:
                datetime.fromisoformat(due_date.replace("Z", "+00:00"))
            except ValueError:
                raise PlannerError("Invalid due date format")

        body = {"planId": plan_id, "bucketId": bucket_id, "title": title}

        if assignments:
            body["assignments"] = assignments
        if due_date:
            body["dueDateTime"] = due_date
        if priority is not None:
            body["priority"] = priority

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_task(response.json())

    except requests.RequestException as e:
        raise PlannerError(f"Network error while creating task: {str(e)}")


def update_task(
    current_user: str,
    task_id: str,
    e_tag: str,
    update_fields: Dict,
    access_token: str = None,
) -> Dict:
    """
    Updates a task with ETag concurrency control.

    Args:
        current_user: User identifier
        task_id: Task ID
        e_tag: Current ETag of the task
        update_fields: Fields to update

    Returns:
        Dict containing updated task details

    Raises:
        TaskNotFoundError: If task doesn't exist
        ETagError: If ETag doesn't match
        PlannerError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/planner/tasks/{task_id}"

        headers = {"If-Match": e_tag}

        # Validate certain update fields
        if "priority" in update_fields:
            priority = update_fields["priority"]
            if not (0 <= priority <= 10):
                raise PlannerError("Priority must be between 0 and 10")

        if "dueDateTime" in update_fields:
            try:
                datetime.fromisoformat(
                    update_fields["dueDateTime"].replace("Z", "+00:00")
                )
            except ValueError:
                raise PlannerError("Invalid due date format")

        response = session.patch(url, headers=headers, json=update_fields)

        if not response.ok:
            handle_graph_error(response)

        return format_task(response.json())

    except requests.RequestException as e:
        raise PlannerError(f"Network error while updating task: {str(e)}")


def delete_task(
    current_user: str, task_id: str, e_tag: str, access_token: str = None
) -> Dict:
    """
    Deletes a task with ETag concurrency control.

    Args:
        current_user: User identifier
        task_id: Task ID
        e_tag: Current ETag of the task

    Returns:
        Dict containing deletion status

    Raises:
        TaskNotFoundError: If task doesn't exist
        ETagError: If ETag doesn't match
        PlannerError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/planner/tasks/{task_id}"

        headers = {"If-Match": e_tag}

        response = session.delete(url, headers=headers)

        if response.status_code == 204:
            return {"status": "deleted", "id": task_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise PlannerError(f"Network error while deleting task: {str(e)}")


def format_plan(plan: Dict) -> Dict:
    """Format plan data consistently"""
    return {
        "id": plan["id"],
        "title": plan.get("title", ""),
        "createdBy": plan.get("createdBy", {}).get("user", {}).get("displayName", ""),
        "createdDateTime": plan.get("createdDateTime", ""),
        "owner": plan.get("owner", ""),
        "container": {
            "containerId": plan.get("container", {}).get("containerId", ""),
            "type": plan.get("container", {}).get("type", ""),
        },
    }


def format_bucket(bucket: Dict) -> Dict:
    """Format bucket data consistently"""
    return {
        "id": bucket["id"],
        "name": bucket.get("name", ""),
        "planId": bucket.get("planId", ""),
        "orderHint": bucket.get("orderHint", ""),
        "createdDateTime": bucket.get("createdDateTime", ""),
    }


def format_task(task: Dict) -> Dict:
    """Format task data consistently"""
    return {
        "id": task["id"],
        "planId": task.get("planId", ""),
        "bucketId": task.get("bucketId", ""),
        "title": task.get("title", ""),
        "percentComplete": task.get("percentComplete", 0),
        "priority": task.get("priority", 0),
        "dueDateTime": task.get("dueDateTime", ""),
        "createdDateTime": task.get("createdDateTime", ""),
        "assignments": task.get("assignments", {}),
        "orderHint": task.get("orderHint", ""),
        "assigneePriority": task.get("assigneePriority", ""),
        "etag": task.get("@odata.etag", ""),
    }


def get_task_details(current_user, task_id, access_token):
    session = get_ms_graph_session(current_user, integration_name, access_token)
    url = f"{GRAPH_ENDPOINT}/planner/tasks/{task_id}"
    response = session.get(url)
    response.raise_for_status()
    return response.json()
