import json
import requests
from typing import Dict, List, Optional, Union, BinaryIO
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_drive"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
MAX_SIMPLE_UPLOAD_SIZE = 4 * 1024 * 1024  # 4 MB

from pycommon.logger import getLogger
logger = getLogger(integration_name)

class DriveError(Exception):
    """Base exception for drive operations"""

    pass


class ItemNotFoundError(DriveError):
    """Raised when a drive item cannot be found"""

    pass


class FileSizeError(DriveError):
    """Raised when file size exceeds limits"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        raise ItemNotFoundError("Drive item not found")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise DriveError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_drive_items(
    current_user: str,
    folder_id: str = "root",
    page_size: int = 999,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists items in the specified OneDrive folder with pagination support.

    Args:
        current_user: User identifier
        folder_id: ID of the folder to list (default: "root")
        page_size: Number of items per page

    Returns:
        List of drive items

    Raises:
        ItemNotFoundError: If folder doesn't exist
        DriveError: For other retrieval failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        # Request sensitivity label along with other metadata
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}/children?$top={page_size}&$select=id,name,size,file,folder,createdDateTime,lastModifiedDateTime,shared,createdBy,lastModifiedBy,sensitivityLabel"

        all_items = []
        while url:
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)

            data = response.json()
            items = data.get("value", [])

            # Process each item
            formatted_items = []
            for item in items:
                # Log raw item to see what Graph API returns
                logger.info(f"RAW ITEM FROM GRAPH API: {json.dumps(item, indent=2)}")

                # Specifically check for sensitivityLabel
                if "sensitivityLabel" in item:
                    logger.info(f"✓ FOUND sensitivityLabel in item {item.get('name')}: {item['sensitivityLabel']}")
                else:
                    logger.warning(f"✗ NO sensitivityLabel in item {item.get('name')} - Graph API did not return it!")

                # Format the basic item data
                formatted_item = format_drive_item(item)

                # Log formatted item to see what we're returning
                logger.info(f"FORMATTED ITEM AFTER format_drive_item: {json.dumps(formatted_item, indent=2)}")

                formatted_items.append(formatted_item)

            all_items.extend(formatted_items)

            # Handle pagination
            url = data.get("@odata.nextLink")

        # Log before convert_dictionaries
        logger.info(f"BEFORE convert_dictionaries: {json.dumps(all_items, indent=2)}")

        # Convert to standardized format before returning
        result = convert_dictionaries(all_items)

        # Log after convert_dictionaries
        logger.info(f"AFTER convert_dictionaries: {json.dumps(result, indent=2)}")

        return result

    except requests.RequestException as e:
        raise DriveError(f"Network error while listing drive items: {str(e)}")


def upload_file(
    current_user: str,
    file_path: str,
    file_content: Union[str, bytes, BinaryIO],
    folder_id: str = "root",
    access_token: str = None,
) -> Dict:
    """
    Uploads a file to OneDrive with size-based upload strategy.

    Args:
        current_user: User identifier
        file_path: Path where to store the file (including filename)
        file_content: Content to upload
        folder_id: Parent folder ID (default: root)

    Returns:
        Dict containing uploaded file details

    Raises:
        DriveError: If upload fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Determine content size
        if hasattr(file_content, "seek") and hasattr(file_content, "tell"):
            file_content.seek(0, 2)  # Seek to end
            content_size = file_content.tell()
            file_content.seek(0)  # Reset position
        else:
            content_size = len(file_content)

        if content_size <= MAX_SIMPLE_UPLOAD_SIZE:
            return _simple_upload(session, file_path, file_content, folder_id)
        else:
            return _large_file_upload(
                session, file_path, file_content, content_size, folder_id
            )

    except requests.RequestException as e:
        raise DriveError(f"Network error while uploading file: {str(e)}")


def _simple_upload(
    session: requests.Session,
    file_path: str,
    content: Union[str, bytes, BinaryIO],
    folder_id: str,
) -> Dict:
    """Helper function for simple file uploads"""
    url = f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}:/{file_path}:/content"
    response = session.put(url, data=content)

    if not response.ok:
        handle_graph_error(response)

    return format_drive_item(response.json())


def _large_file_upload(
    session: requests.Session,
    file_path: str,
    content: Union[str, bytes, BinaryIO],
    content_size: int,
    folder_id: str,
) -> Dict:
    """Helper function for large file uploads using upload sessions"""
    # Create upload session
    url = (
        f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}:/{file_path}:/createUploadSession"
    )
    response = session.post(url)

    if not response.ok:
        handle_graph_error(response)

    upload_url = response.json().get("uploadUrl")
    if not upload_url:
        raise DriveError("Failed to create upload session")

    # Upload file in chunks
    chunk_size = 327680  # 320 KB chunks
    for start in range(0, content_size, chunk_size):
        chunk_end = min(start + chunk_size, content_size)
        content_range = f"bytes {start}-{chunk_end-1}/{content_size}"

        if hasattr(content, "read"):
            chunk = content.read(chunk_size)
        else:
            chunk = content[start:chunk_end]

        headers = {"Content-Range": content_range}
        response = session.put(upload_url, data=chunk, headers=headers)

        if not response.ok and response.status_code != 202:  # 202 means chunk accepted
            handle_graph_error(response)

    return format_drive_item(response.json())


def download_file(current_user: str, item_id: str, access_token: str = None) -> Dict:
    """
    Gets a download URL for a file from OneDrive instead of returning raw bytes.

    SECURITY: Blocks access to Level 4 (Critical/Confidential) files.

    Args:
        current_user: User identifier
        item_id: ID of the file to download
        access_token: Optional OAuth token

    Returns:
        Dict with download URL and file info

    Raises:
        ItemNotFoundError: If file doesn't exist
        DriveError: For other download failures, including sensitivity restrictions
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Get file metadata including sensitivity label
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}"
        params = {"$select": "id,name,file,sensitivityLabel,@microsoft.graph.downloadUrl"}
        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        file_data = response.json()

        # SECURITY CHECK: Parse sensitivity label and block Level 4 files
        sensitivity_info = parse_file_sensitivity_label(file_data)

        if sensitivity_info and sensitivity_info.get("is_sensitive"):
            file_name = file_data.get("name", "unknown")
            logger.warning(f"🚫 BLOCKED DOWNLOAD ATTEMPT - Level 4 file: {file_name}, User: {current_user}")
            raise DriveError(
                "This file contains sensitive data (Level 4 - Critical/Confidential) and cannot be downloaded. "
                "Access to this file is restricted for security compliance."
            )

        # Return a dict with the download URL and file metadata
        return {
            "id": file_data.get("id"),
            "name": file_data.get("name"),
            "mimeType": file_data.get("file", {}).get("mimeType", ""),
            "downloadLink": file_data.get("@microsoft.graph.downloadUrl"),
        }

    except requests.RequestException as e:
        raise DriveError(f"Network error while getting download URL: {str(e)}")


def delete_item(current_user: str, item_id: str, access_token: str) -> Dict:
    """
    Deletes a file or folder from OneDrive.

    Args:
        current_user: User identifier
        item_id: ID of the item to delete

    Returns:
        Dict containing deletion status

    Raises:
        ItemNotFoundError: If item doesn't exist
        DriveError: For other deletion failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": item_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise DriveError(f"Network error while deleting item: {str(e)}")


def convert_dictionaries(input_list: List[Dict]) -> List[Dict]:
    """
    Convert a list of OneDrive item dictionaries to a standardized format.

    Args:
        input_list: List of drive items to convert

    Returns:
        List of standardized dictionaries with consistent format
    """
    result = []
    for item in input_list:
        # Extract basic information
        name = item.get("name", "")
        mimeType = item.get("type", "")
        # Add slash prefix to folder names like in Google Drive
        if mimeType == "folder":
            name = "/" + name

        # Create standardized dictionary with required fields
        file_data = {
            "id": item.get("id", ""),
            "name": name,
            "mimeType": mimeType,
        }

        # Add size if available
        if "size" in item:
            file_data["size"] = item.get("size", 0)

        # Pass through sensitivity fields if present
        if "sensitivity" in item:
            file_data["sensitivity"] = item.get("sensitivity")
        if "sensitivityLabel" in item:
            file_data["sensitivityLabel"] = item.get("sensitivityLabel")
        if "attentionNote" in item:
            file_data["attentionNote"] = item.get("attentionNote")

        result.append(file_data)
    return result


def parse_file_sensitivity_label(item: Dict) -> Optional[Dict]:
    """
    Parse Microsoft Information Protection label from OneDrive/SharePoint file.

    Args:
        item: Raw drive item data from Graph API

    Returns:
        Dict containing sensitivity level and label information, or None if no label
    """
    # Check for sensitivityLabel property (Graph API v1.0+)
    if "sensitivityLabel" in item:
        label = item["sensitivityLabel"]
        # Graph API uses 'displayName' not 'name'
        label_name = label.get("displayName", label.get("name", "")).lower()
        label_id = label.get("id", "")

        # Only process if there's an actual label (not empty)
        if not label_name or not label_id:
            return None

        # IMPORTANT: Check exact "level X" patterns FIRST to avoid false matches
        # E.g., "Level 3 Restricted" should match Level 3, not Level 4's "restricted" keyword

        # Level 4 (exact match first)
        if "level 4" in label_name:
            logger.warning(f"Level 4 sensitive file detected: {item.get('name', 'unknown')}")
            return {
                "level": 4,
                "label": "confidential",
                "is_sensitive": True,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 3 (exact match first)
        if "level 3" in label_name:
            return {
                "level": 3,
                "label": "private",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 2 (exact match first)
        if "level 2" in label_name:
            return {
                "level": 2,
                "label": "personal",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 1 (exact match first)
        if "level 1" in label_name:
            return {
                "level": 1,
                "label": "normal",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Fallback: Check other keywords if no exact "level X" match
        level_4_keywords = ["critical", "confidential", "restricted", "secret",
            "highly confidential", "classified", "sensitive", "proprietary"]
        if any(keyword in label_name for keyword in level_4_keywords):
            logger.warning(f"Level 4 sensitive file detected: {item.get('name', 'unknown')}")
            return {
                "level": 4,
                "label": "confidential",
                "is_sensitive": True,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 3 keywords
        level_3_keywords = ["internal", "private", "company", "organization"]
        if any(keyword in label_name for keyword in level_3_keywords):
            return {
                "level": 3,
                "label": "private",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 2 keywords (fallback)
        if "personal" in label_name:
            return {
                "level": 2,
                "label": "personal",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

        # Level 1 keywords (fallback)
        level_1_keywords = ["public", "non-sensitive", "general", "unrestricted"]
        if any(keyword in label_name for keyword in level_1_keywords):
            return {
                "level": 1,
                "label": "normal",
                "is_sensitive": False,
                "displayName": label.get("displayName"),
                "labelId": label_id
            }

    return None


def format_drive_item(item: Dict) -> Dict:
    """
    Format drive item data consistently.

    Args:
        item: Raw drive item data from Graph API

    Returns:
        Dict containing formatted item details
    """
    # Parse sensitivity label for files (not folders)
    sensitivity_info = None
    if item.get("file"):  # Only check files, not folders
        sensitivity_info = parse_file_sensitivity_label(item)

    formatted = {
        "id": item["id"],
        "name": item.get("name", ""),
        "size": item.get("size", 0),
        "downloadUrl": item.get(
            "@microsoft.graph.downloadUrl"
        ),  # This may be None for folder listings
        "createdDateTime": item.get("createdDateTime", ""),
        "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
        "type": "folder" if item.get("folder") else "file",
        "mimeType": (
            item.get("file", {}).get("mimeType", "") if item.get("file") else ""
        ),
        # 'parentReference': item.get('parentReference', {}),
        "shared": bool(item.get("shared")),
        "createdBy": item.get("createdBy", {}).get("user", {}).get("displayName", ""),
        "lastModifiedBy": item.get("lastModifiedBy", {})
        .get("user", {})
        .get("displayName", ""),
    }

    # Add sensitivity fields only if label exists
    if sensitivity_info:
        formatted["sensitivity"] = sensitivity_info["level"]
        formatted["sensitivityLabel"] = sensitivity_info["label"]

        # Add attention note for level 4 sensitive files
        if sensitivity_info["is_sensitive"]:
            formatted["attentionNote"] = "This file contains sensitive data and cannot be viewed."
            formatted["downloadUrl"] = None  # Block download URL for sensitive files

    return formatted


def get_drive_item(current_user: str, item_id: str, access_token: str) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return format_drive_item(response.json())
    except requests.RequestException as e:
        raise DriveError(f"Network error while retrieving drive item: {str(e)}")


def create_folder(
    current_user: str,
    folder_name: str,
    parent_folder_id: str = "root",
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{parent_folder_id}/children"
        payload = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename",
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_drive_item(response.json())
    except requests.RequestException as e:
        raise DriveError(f"Network error while creating folder: {str(e)}")


def update_drive_item(
    current_user: str, item_id: str, updates: Dict, access_token: str = None
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}"
        response = session.patch(url, json=updates)
        if not response.ok:
            logger.warning("Drive item update failed. Status: %s, Response: %s", response.status_code, response.text)
            handle_graph_error(response)
        return format_drive_item(response.json())
    except requests.RequestException as e:
        raise DriveError(f"Network error while updating drive item: {str(e)}")


def copy_drive_item(
    current_user: str,
    item_id: str,
    new_name: str,
    parent_folder_id: str = "root",
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/copy"
        payload = {"parentReference": {"id": parent_folder_id}, "name": new_name}
        response = session.post(url, json=payload)
        # Copy operations are asynchronous; a 202 status indicates initiation
        if response.status_code == 202:
            location = response.headers.get("Location")
            return {"status": "copy initiated", "location": location}
        elif response.ok:
            return format_drive_item(response.json())
        else:
            handle_graph_error(response)
    except requests.RequestException as e:
        raise DriveError(f"Network error while copying drive item: {str(e)}")


def move_drive_item(
    current_user: str, item_id: str, new_parent_id: str, access_token: str = None
) -> Dict:
    try:
        updates = {"parentReference": {"id": new_parent_id}}
        return update_drive_item(current_user, item_id, updates, access_token)
    except requests.RequestException as e:
        raise DriveError(f"Network error while moving drive item: {str(e)}")


def create_sharing_link(
    current_user: str,
    item_id: str,
    link_type: str = "view",
    scope: str = "anonymous",
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/createLink"
        payload = {"type": link_type, "scope": scope}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return response.json().get("link", {})
    except requests.RequestException as e:
        raise DriveError(f"Network error while creating sharing link: {str(e)}")


def invite_to_drive_item(
    current_user: str,
    item_id: str,
    recipients: List[Dict],
    message: str = "",
    require_sign_in: bool = True,
    send_invitation: bool = True,
    roles: Optional[List[str]] = None,
    access_token: str = None,
) -> Dict:
    if roles is None:
        roles = ["read"]
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/invite"
        payload = {
            "recipients": recipients,
            "message": message,
            "requireSignIn": require_sign_in,
            "sendInvitation": send_invitation,
            "roles": roles,
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise DriveError(f"Network error while inviting to drive item: {str(e)}")
