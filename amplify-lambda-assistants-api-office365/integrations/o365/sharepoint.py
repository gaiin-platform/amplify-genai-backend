import json
import requests
from typing import Dict, List, Optional, Union, BinaryIO
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_sharepoint"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"

# The site-id is often in this format: tenant.sharepoint.com,GUID,GUID.
# The list-id can be a GUID as well.
# You frequently discover these IDs via the list_sites and list_site_lists calls,
#  or by using GET /sites/root or GET /sites/{hostname}:{sitePath} to look up a site by URL.


class SharePointError(Exception):
    """Base exception for SharePoint operations"""

    pass


class SiteNotFoundError(SharePointError):
    """Raised when a site cannot be found"""

    pass


class ListNotFoundError(SharePointError):
    """Raised when a list cannot be found"""

    pass


class ItemNotFoundError(SharePointError):
    """Raised when a list item cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "site" in error_message:
            raise SiteNotFoundError("Site not found")
        elif "list" in error_message:
            raise ListNotFoundError("List not found")
        elif "item" in error_message:
            raise ItemNotFoundError("Item not found")
        raise SharePointError("Resource not found")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise SharePointError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_sites(
    current_user: str,
    search_query: Optional[str] = None,
    top: int = 10,
    skip: int = 0,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists SharePoint sites with search and pagination support.

    Args:
        current_user: User identifier
        search_query: Optional search term
        top: Maximum number of sites to retrieve
        skip: Number of sites to skip

    Returns:
        List of site details

    Raises:
        SharePointError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        params = {"$top": top, "$skip": skip, "$orderby": "name"}

        if search_query:
            url = f"{GRAPH_ENDPOINT}/sites?search={search_query}"
        else:
            url = f"{GRAPH_ENDPOINT}/sites"

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        sites = response.json().get("value", [])
        return [format_site(site) for site in sites]

    except requests.RequestException as e:
        raise SharePointError(f"Network error while listing sites: {str(e)}")


def get_site_by_path(
    current_user: str,
    hostname: str,
    site_path: Optional[str] = None,
    access_token: str = None,
) -> Dict:
    """
    Gets a site by its hostname and optional path.

    Args:
        current_user: User identifier
        hostname: SharePoint hostname
        site_path: Optional site path

    Returns:
        Dict containing site details

    Raises:
        SiteNotFoundError: If site doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        if site_path:
            url = f"{GRAPH_ENDPOINT}/sites/{hostname}:/{site_path}"
        else:
            url = f"{GRAPH_ENDPOINT}/sites/{hostname}"

        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return format_site(response.json())

    except requests.RequestException as e:
        raise SharePointError(f"Network error while getting site: {str(e)}")


def list_site_lists(
    current_user: str,
    site_id: str,
    top: int = 10,
    skip: int = 0,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists SharePoint lists in a site with pagination.

    Args:
        current_user: User identifier
        site_id: Site ID
        top: Maximum number of lists to retrieve
        skip: Number of lists to skip

    Returns:
        List of list details

    Raises:
        SiteNotFoundError: If site doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/lists"

        params = {"$top": top, "$skip": skip, "$orderby": "displayName"}

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        lists = response.json().get("value", [])
        return [format_list(lst) for lst in lists]

    except requests.RequestException as e:
        raise SharePointError(f"Network error while listing lists: {str(e)}")


def get_list_items(
    current_user: str,
    site_id: str,
    list_id: str,
    expand_fields: bool = True,
    top: int = 10,
    skip: int = 0,
    filter_query: Optional[str] = None,
    access_token: str = None,
) -> List[Dict]:
    """
    Gets items from a SharePoint list with pagination and filtering.

    Args:
        current_user: User identifier
        site_id: Site ID
        list_id: List ID
        expand_fields: Whether to expand field values
        top: Maximum number of items to retrieve
        skip: Number of items to skip
        filter_query: Optional OData filter query

    Returns:
        List of list item details

    Raises:
        SiteNotFoundError: If site doesn't exist
        ListNotFoundError: If list doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/lists/{list_id}/items"

        params = {"$top": top, "$skip": skip}

        if expand_fields:
            params["$expand"] = "fields"
        if filter_query:
            params["$filter"] = filter_query

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        items = response.json().get("value", [])
        return [format_list_item(item) for item in items]

    except requests.RequestException as e:
        raise SharePointError(f"Network error while getting list items: {str(e)}")


def create_list_item(
    current_user: str,
    site_id: str,
    list_id: str,
    fields_dict: Dict,
    access_token: str = None,
) -> Dict:
    """
    Creates a new item in a SharePoint list.

    Args:
        current_user: User identifier
        site_id: Site ID
        list_id: List ID
        fields_dict: Dictionary of field names and values

    Returns:
        Dict containing created item details

    Raises:
        SiteNotFoundError: If site doesn't exist
        ListNotFoundError: If list doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/lists/{list_id}/items"

        if not fields_dict:
            raise SharePointError("Fields dictionary cannot be empty")

        body = {"fields": fields_dict}

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_list_item(response.json())

    except requests.RequestException as e:
        raise SharePointError(f"Network error while creating list item: {str(e)}")


def update_list_item(
    current_user: str,
    site_id: str,
    list_id: str,
    item_id: str,
    fields_dict: Dict,
    access_token: str = None,
) -> Dict:
    """
    Updates an existing SharePoint list item.

    Args:
        current_user: User identifier
        site_id: Site ID
        list_id: List ID
        item_id: Item ID
        fields_dict: Dictionary of field names and values to update

    Returns:
        Dict containing updated item details

    Raises:
        SiteNotFoundError: If site doesn't exist
        ListNotFoundError: If list doesn't exist
        ItemNotFoundError: If item doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields"

        if not fields_dict:
            raise SharePointError("Fields dictionary cannot be empty")

        response = session.patch(url, json=fields_dict)

        if not response.ok:
            handle_graph_error(response)

        return format_list_item(response.json())

    except requests.RequestException as e:
        raise SharePointError(f"Network error while updating list item: {str(e)}")


def delete_list_item(
    current_user: str,
    site_id: str,
    list_id: str,
    item_id: str,
    access_token: str = None,
) -> Dict:
    """
    Deletes an item from a SharePoint list.

    Args:
        current_user: User identifier
        site_id: Site ID
        list_id: List ID
        item_id: Item ID

    Returns:
        Dict containing deletion status

    Raises:
        SiteNotFoundError: If site doesn't exist
        ListNotFoundError: If list doesn't exist
        ItemNotFoundError: If item doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/lists/{list_id}/items/{item_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": item_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise SharePointError(f"Network error while deleting list item: {str(e)}")


def format_site(site: Dict) -> Dict:
    """Format site data consistently"""
    return {
        "id": site["id"],
        "name": site.get("name", ""),
        "displayName": site.get("displayName", ""),
        "description": site.get("description", ""),
        "webUrl": site.get("webUrl", ""),
        "createdDateTime": site.get("createdDateTime", ""),
        "lastModifiedDateTime": site.get("lastModifiedDateTime", ""),
        "root": site.get("root", {}),
    }


def format_list(lst: Dict) -> Dict:
    """Format list data consistently"""
    return {
        "id": lst["id"],
        "name": lst.get("name", ""),
        "displayName": lst.get("displayName", ""),
        "description": lst.get("description", ""),
        "webUrl": lst.get("webUrl", ""),
        "list": {
            "template": lst.get("list", {}).get("template", ""),
            "contentTypesEnabled": lst.get("list", {}).get(
                "contentTypesEnabled", False
            ),
            "hidden": lst.get("list", {}).get("hidden", False),
        },
    }


def format_list_item(item: Dict) -> Dict:
    """Format list item data consistently"""
    return {
        "id": item["id"],
        "contentType": item.get("contentType", {}),
        "createdDateTime": item.get("createdDateTime", ""),
        "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
        "fields": item.get("fields", {}),
        "webUrl": item.get("webUrl", ""),
    }
