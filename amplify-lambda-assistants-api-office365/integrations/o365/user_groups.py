import json
import requests
from typing import Dict, List, Optional, Union
from integrations.oauth import get_ms_graph_session

###
# Notes:
# Creating or deleting users / groups typically requires admin-level permissions in Azure AD.
# The groupTypes property with ["Unified"] indicates an Office 365 Group (which includes a mailbox, calendar, etc.).
# A "Security" group would have "securityEnabled": True and "mailEnabled": False.
###
integration_name = "microsoft_users_groups"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class UserGroupError(Exception):
    """Base exception for user and group operations"""

    pass


class UserNotFoundError(UserGroupError):
    """Raised when a user cannot be found"""

    pass


class GroupNotFoundError(UserGroupError):
    """Raised when a group cannot be found"""

    pass


class PermissionError(UserGroupError):
    """Raised when operation lacks required permissions"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "user" in error_message:
            raise UserNotFoundError("User not found")
        elif "group" in error_message:
            raise GroupNotFoundError("Group not found")
        raise UserGroupError("Resource not found")

    if response.status_code == 403:
        raise PermissionError("Insufficient permissions for this operation")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise UserGroupError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_users(
    current_user: str,
    search_query: Optional[str] = None,
    top: int = 10,
    skip: int = 0,
    order_by: Optional[str] = None,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists users with search, pagination and sorting support.

    Args:
        current_user: User identifier
        search_query: Optional search term
        top: Maximum number of users to retrieve
        skip: Number of users to skip
        order_by: Property to sort by (e.g., 'displayName', 'userPrincipalName')

    Returns:
        List of user details

    Raises:
        UserGroupError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        params = {
            "$top": top,
            "$skip": skip,
            "$select": "id,userPrincipalName,displayName,mail,jobTitle,department,officeLocation",
        }

        if order_by:
            params["$orderby"] = order_by

        if search_query:
            url = f'{GRAPH_ENDPOINT}/users?$search="{search_query}"'
            params["$count"] = "true"
        else:
            url = f"{GRAPH_ENDPOINT}/users"

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        users = response.json().get("value", [])
        return [format_user(user) for user in users]

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while listing users: {str(e)}")


def get_user_details(current_user: str, user_id: str, access_token: str = None) -> Dict:
    """
    Gets detailed information about a specific user.

    Args:
        current_user: User identifier
        user_id: User ID or userPrincipalName

    Returns:
        Dict containing user details

    Raises:
        UserNotFoundError: If user doesn't exist
        UserGroupError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{user_id}"

        params = {
            "$select": (
                "id,userPrincipalName,displayName,mail,jobTitle,"
                "department,officeLocation,mobilePhone,businessPhones,"
                "preferredLanguage,usageLocation"
            )
        }

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        return format_user(response.json(), detailed=True)

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while getting user details: {str(e)}")


def list_groups(
    current_user: str,
    search_query: Optional[str] = None,
    group_type: Optional[str] = None,
    top: int = 10,
    skip: int = 0,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists groups with filtering and pagination support.

    Args:
        current_user: User identifier
        search_query: Optional search term
        group_type: Optional group type filter ('Unified', 'Security')
        top: Maximum number of groups to retrieve
        skip: Number of groups to skip

    Returns:
        List of group details

    Raises:
        UserGroupError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        params = {
            "$top": top,
            "$skip": skip,
            "$select": "id,displayName,description,mail,groupTypes,securityEnabled,visibility",
        }

        if group_type:
            if group_type == "Unified":
                params["$filter"] = "groupTypes/any(t:t eq 'Unified')"
            elif group_type == "Security":
                params["$filter"] = "securityEnabled eq true"

        if search_query:
            url = f'{GRAPH_ENDPOINT}/groups?$search="{search_query}"'
            params["$count"] = "true"
        else:
            url = f"{GRAPH_ENDPOINT}/groups"

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        groups = response.json().get("value", [])
        return [format_group(group) for group in groups]

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while listing groups: {str(e)}")


def get_group_details(
    current_user: str, group_id: str, access_token: str = None
) -> Dict:
    """
    Gets detailed information about a specific group.

    Args:
        current_user: User identifier
        group_id: Group ID

    Returns:
        Dict containing group details

    Raises:
        GroupNotFoundError: If group doesn't exist
        UserGroupError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/groups/{group_id}"

        params = {
            "$select": (
                "id,displayName,description,mail,groupTypes,"
                "securityEnabled,visibility,createdDateTime,"
                "membershipRule,membershipRuleProcessingState"
            )
        }

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        return format_group(response.json(), detailed=True)

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while getting group details: {str(e)}")


def create_group(
    current_user: str,
    display_name: str,
    mail_nickname: str,
    group_type: str = "Unified",
    description: Optional[str] = None,
    owners: Optional[List[str]] = None,
    members: Optional[List[str]] = None,
    access_token: str = None,
) -> Dict:
    """
    Creates a new group.

    Args:
        current_user: User identifier
        display_name: Group display name
        mail_nickname: Mail nickname
        group_type: Group type ('Unified' or 'Security')
        description: Optional group description
        owners: Optional list of owner user IDs
        members: Optional list of member user IDs

    Returns:
        Dict containing created group details

    Raises:
        PermissionError: If user lacks required permissions
        UserGroupError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/groups"

        if not display_name or not mail_nickname:
            raise UserGroupError("Display name and mail nickname are required")

        body = {
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "mailEnabled": group_type == "Unified",
            "securityEnabled": True if group_type == "Security" else False,
            "groupTypes": ["Unified"] if group_type == "Unified" else [],
        }

        if description:
            body["description"] = description
        if owners:
            body["owners@odata.bind"] = [
                f"{GRAPH_ENDPOINT}/users/{owner}" for owner in owners
            ]
        if members:
            body["members@odata.bind"] = [
                f"{GRAPH_ENDPOINT}/users/{member}" for member in members
            ]

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_group(response.json())

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while creating group: {str(e)}")


def delete_group(current_user: str, group_id: str, access_token: str = None) -> Dict:
    """
    Deletes a group.

    Args:
        current_user: User identifier
        group_id: Group ID to delete

    Returns:
        Dict containing deletion status

    Raises:
        GroupNotFoundError: If group doesn't exist
        PermissionError: If user lacks required permissions
        UserGroupError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/groups/{group_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": group_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise UserGroupError(f"Network error while deleting group: {str(e)}")


def format_user(user: Dict, detailed: bool = False) -> Dict:
    """Format user data consistently"""
    formatted = {
        "id": user["id"],
        "userPrincipalName": user.get("userPrincipalName", ""),
        "displayName": user.get("displayName", ""),
        "mail": user.get("mail", ""),
        "jobTitle": user.get("jobTitle", ""),
        "department": user.get("department", ""),
        "officeLocation": user.get("officeLocation", ""),
    }

    if detailed:
        formatted.update(
            {
                "mobilePhone": user.get("mobilePhone", ""),
                "businessPhones": user.get("businessPhones", []),
                "preferredLanguage": user.get("preferredLanguage", ""),
                "usageLocation": user.get("usageLocation", ""),
                "accountEnabled": user.get("accountEnabled", True),
            }
        )

    return formatted


def format_group(group: Dict, detailed: bool = False) -> Dict:
    """Format group data consistently"""
    formatted = {
        "id": group["id"],
        "displayName": group.get("displayName", ""),
        "description": group.get("description", ""),
        "mail": group.get("mail", ""),
        "groupTypes": group.get("groupTypes", []),
        "securityEnabled": group.get("securityEnabled", False),
        "visibility": group.get("visibility", "Private"),
    }

    if detailed:
        formatted.update(
            {
                "createdDateTime": group.get("createdDateTime", ""),
                "membershipRule": group.get("membershipRule", ""),
                "membershipRuleProcessingState": group.get(
                    "membershipRuleProcessingState", ""
                ),
                "renewedDateTime": group.get("renewedDateTime", ""),
                "deletedDateTime": group.get("deletedDateTime", ""),
                "classification": group.get("classification", ""),
            }
        )

    return formatted
