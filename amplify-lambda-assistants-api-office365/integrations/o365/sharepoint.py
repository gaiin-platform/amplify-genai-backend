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

    Note: When no search_query is provided, returns the root site and commonly used sites.
    Microsoft Graph API requires search queries to list sites broadly.

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

        if search_query:
            # Use search endpoint for specific queries
            url = f"{GRAPH_ENDPOINT}/sites?search={search_query}"
            params = {"$top": top}
        else:
            # When no search query, get root site + followed sites
            # This provides a better user experience than empty results
            sites = []

            # Get root site
            try:
                root_response = session.get(f"{GRAPH_ENDPOINT}/sites/root")
                if root_response.ok:
                    sites.append(root_response.json())
            except Exception as e:
                print(f"Could not fetch root site: {e}")

            # Also try to get followed/frequently used sites using search with wildcard
            try:
                search_url = f"{GRAPH_ENDPOINT}/sites?search=*"
                search_params = {"$top": top - len(sites) if sites else top}
                search_response = session.get(search_url, params=search_params)
                if search_response.ok:
                    search_sites = search_response.json().get("value", [])
                    sites.extend(search_sites)
            except Exception as e:
                print(f"Could not fetch additional sites: {e}")

            return [format_site(site) for site in sites]

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

        params = {"$top": top}

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

        params = {"$top": top}

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


# Document Library Functions for Drive Integration

def list_document_libraries(
    current_user: str,
    site_id: str,
    top: int = 25,
    skip: int = 0,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists document libraries in a SharePoint site.
    
    Args:
        current_user: User identifier
        site_id: Site ID
        top: Maximum number of libraries to retrieve
        skip: Number of libraries to skip
        
    Returns:
        List of document library details
        
    Raises:
        SiteNotFoundError: If site doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives"

        params = {"$top": top}

        response = session.get(url, params=params)
        
        if not response.ok:
            handle_graph_error(response)
        
        drives = response.json().get("value", [])
        # Filter to document libraries only (driveType: documentLibrary)
        libraries = [drive for drive in drives if drive.get("driveType") == "documentLibrary"]
        return [format_document_library(lib) for lib in libraries]
        
    except requests.RequestException as e:
        raise SharePointError(f"Network error while listing document libraries: {str(e)}")


def list_library_files(
    current_user: str,
    site_id: str,
    drive_id: str,
    folder_path: str = "root",
    top: int = 100,
    skip: int = 0,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists files in a document library folder.
    
    Args:
        current_user: User identifier
        site_id: Site ID
        drive_id: Document library (drive) ID
        folder_path: Folder path or "root" for root folder
        top: Maximum number of files to retrieve
        skip: Number of files to skip
        
    Returns:
        List of file details
        
    Raises:
        SiteNotFoundError: If site doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        if folder_path == "root":
            url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/root/children"
        else:
            url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/root:/{folder_path}:/children"

        params = {"$top": top, "$orderby": "name"}

        response = session.get(url, params=params)
        
        if not response.ok:
            handle_graph_error(response)
        
        items = response.json().get("value", [])
        return [format_drive_item(item) for item in items]
        
    except requests.RequestException as e:
        raise SharePointError(f"Network error while listing library files: {str(e)}")


def get_file_download_url(
    current_user: str,
    site_id: str,
    drive_id: str,
    item_id: str,
    access_token: str = None,
) -> Dict:
    """
    Gets download URL for a SharePoint file.

    Args:
        current_user: User identifier
        site_id: Site ID
        drive_id: Document library (drive) ID
        item_id: File item ID

    Returns:
        Dict containing download URL and file metadata

    Raises:
        ItemNotFoundError: If file doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Get file metadata first
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
        params = {"$select": "id,name,size,createdDateTime,lastModifiedDateTime,@microsoft.graph.downloadUrl,file"}

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        item = response.json()

        # Try to get @microsoft.graph.downloadUrl, but if it's not available,
        # use the /content endpoint as fallback
        download_url = item.get("@microsoft.graph.downloadUrl")
        if not download_url:
            # Use the content endpoint which always works for authenticated requests
            download_url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/items/{item_id}/content"

        return {
            "id": item["id"],
            "name": item["name"],
            "downloadLink": download_url,
            "mimeType": item.get("file", {}).get("mimeType", "application/octet-stream"),
            "size": item.get("size", 0),
            "createdDateTime": item.get("createdDateTime"),
            "lastModifiedDateTime": item.get("lastModifiedDateTime"),
        }

    except requests.RequestException as e:
        raise SharePointError(f"Network error while getting file download URL: {str(e)}")


def get_drive_item_metadata(
    current_user: str,
    site_id: str,
    drive_id: str,
    item_id: str,
    access_token: str = None,
) -> Dict:
    """
    Gets metadata for a SharePoint drive item.
    
    Args:
        current_user: User identifier
        site_id: Site ID
        drive_id: Document library (drive) ID
        item_id: Item ID
        
    Returns:
        Dict containing file metadata
        
    Raises:
        ItemNotFoundError: If item doesn't exist
        SharePointError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        
        url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
        
        response = session.get(url)
        
        if not response.ok:
            handle_graph_error(response)
        
        return format_drive_item(response.json())
        
    except requests.RequestException as e:
        raise SharePointError(f"Network error while getting drive item metadata: {str(e)}")


def upload_file_to_library(
    current_user: str,
    site_id: str,
    drive_id: str,
    file_name: str,
    file_content: Union[bytes, BinaryIO],
    folder_path: str = "root",
    access_token: str = None,
) -> Dict:
    """
    Uploads a file to a SharePoint document library.
    
    Args:
        current_user: User identifier
        site_id: Site ID
        drive_id: Document library (drive) ID
        file_name: Name for the uploaded file
        file_content: File content as bytes or file-like object
        folder_path: Target folder path or "root" for root folder
        
    Returns:
        Dict containing uploaded file details
        
    Raises:
        SharePointError: For upload failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        
        # Convert file content to bytes if needed
        if hasattr(file_content, 'read'):
            file_data = file_content.read()
        else:
            file_data = file_content
        
        if folder_path == "root":
            url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/root:/{file_name}:/content"
        else:
            url = f"{GRAPH_ENDPOINT}/sites/{site_id}/drives/{drive_id}/root:/{folder_path}/{file_name}:/content"
        
        headers = {'Content-Type': 'application/octet-stream'}
        
        response = session.put(url, data=file_data, headers=headers)
        
        if not response.ok:
            handle_graph_error(response)
        
        return format_drive_item(response.json())
        
    except requests.RequestException as e:
        raise SharePointError(f"Network error while uploading file: {str(e)}")


def get_all_library_files_recursively(
    current_user: str,
    site_id: str,
    drive_id: str,
    folder_path: str = "root",
    access_token: str = None,
    visited_folders: Optional[set] = None,
) -> List[Dict]:
    """
    Recursively gets all files from a document library folder and subfolders.
    
    Args:
        current_user: User identifier
        site_id: Site ID
        drive_id: Document library (drive) ID
        folder_path: Starting folder path or "root"
        visited_folders: Set to track visited folders (prevents infinite recursion)
        
    Returns:
        Flat list of all files in folder tree
        
    Raises:
        SharePointError: For retrieval failures
    """
    if visited_folders is None:
        visited_folders = set()
    
    # Prevent infinite recursion
    folder_key = f"{drive_id}:{folder_path}"
    if folder_key in visited_folders:
        return []
    visited_folders.add(folder_key)
    
    all_files = []
    
    try:
        # Get all items in current folder
        items = list_library_files(
            current_user, site_id, drive_id, folder_path, 
            top=1000, access_token=access_token
        )
        
        for item in items:
            if item.get("folder"):
                # It's a folder - recursively get files from it
                subfolder_path = f"{folder_path}/{item['name']}" if folder_path != "root" else item["name"]
                subfolder_files = get_all_library_files_recursively(
                    current_user, site_id, drive_id, subfolder_path, 
                    access_token, visited_folders
                )
                all_files.extend(subfolder_files)
            else:
                # It's a file - add to collection
                all_files.append(item)
        
    except Exception as e:
        # Log error but don't fail completely
        print(f"Error processing folder {folder_path}: {e}")
    
    return all_files


def format_document_library(library: Dict) -> Dict:
    """Format document library data consistently"""
    return {
        "id": library["id"],
        "name": library.get("name", ""),
        "description": library.get("description", ""),
        "webUrl": library.get("webUrl", ""),
        "driveType": library.get("driveType", ""),
        "createdDateTime": library.get("createdDateTime", ""),
        "lastModifiedDateTime": library.get("lastModifiedDateTime", ""),
        "owner": library.get("owner", {}),
        "quota": library.get("quota", {}),
    }


def format_drive_item(item: Dict) -> Dict:
    """Format drive item (file/folder) data consistently"""
    # Determine mimeType: use file mimeType or folder type
    if item.get("folder"):
        mime_type = "application/vnd.google-apps.folder"
    else:
        mime_type = item.get("file", {}).get("mimeType", "application/octet-stream")

    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "size": item.get("size", 0),
        "createdDateTime": item.get("createdDateTime", ""),
        "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
        "webUrl": item.get("webUrl", ""),
        "downloadUrl": item.get("@microsoft.graph.downloadUrl"),
        "mimeType": mime_type,
        "folder": item.get("folder"),  # Will be None for files, object for folders
        "file": item.get("file"),      # Will be None for folders, object for files
        "createdBy": item.get("createdBy", {}),
        "lastModifiedBy": item.get("lastModifiedBy", {}),
        "parentReference": item.get("parentReference", {}),
    }
