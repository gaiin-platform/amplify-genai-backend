import json
import requests
from typing import Dict, List, Optional, Union, BinaryIO
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_onedrive"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
MAX_SIMPLE_UPLOAD_SIZE = 4 * 1024 * 1024  # 4 MB

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
        error_message = error_data.get('error', {}).get('message', 'Unknown error')
    except json.JSONDecodeError:
        error_message = response.text
    raise DriveError(f"Graph API error: {error_message} (Status: {response.status_code})")

def list_drive_items(current_user: str, folder_id: str = "root", 
                    page_size: int = 25, access_token: str = None) -> List[Dict]:
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
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}/children?$top={page_size}"
        
        all_items = []
        while url:
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)
                
            data = response.json()
            items = data.get('value', [])
            all_items.extend([format_drive_item(item) for item in items])
            
            # Handle pagination
            url = data.get('@odata.nextLink')
            
        return all_items
        
    except requests.RequestException as e:
        raise DriveError(f"Network error while listing drive items: {str(e)}")

def upload_file(current_user: str, file_path: str, 
                file_content: Union[str, bytes, BinaryIO], 
                folder_id: str = "root", access_token: str = None) -> Dict:
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
        if hasattr(file_content, 'seek') and hasattr(file_content, 'tell'):
            file_content.seek(0, 2)  # Seek to end
            content_size = file_content.tell()
            file_content.seek(0)  # Reset position
        else:
            content_size = len(file_content)
            
        if content_size <= MAX_SIMPLE_UPLOAD_SIZE:
            return _simple_upload(session, file_path, file_content, folder_id)
        else:
            return _large_file_upload(session, file_path, file_content, content_size, folder_id)
            
    except requests.RequestException as e:
        raise DriveError(f"Network error while uploading file: {str(e)}")

def _simple_upload(session: requests.Session, file_path: str, 
                  content: Union[str, bytes, BinaryIO], 
                  folder_id: str) -> Dict:
    """Helper function for simple file uploads"""
    url = f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}:/{file_path}:/content"
    response = session.put(url, data=content)
    
    if not response.ok:
        handle_graph_error(response)
        
    return format_drive_item(response.json())

def _large_file_upload(session: requests.Session, file_path: str, 
                      content: Union[str, bytes, BinaryIO], 
                      content_size: int, folder_id: str) -> Dict:
    """Helper function for large file uploads using upload sessions"""
    # Create upload session
    url = f"{GRAPH_ENDPOINT}/me/drive/items/{folder_id}:/{file_path}:/createUploadSession"
    response = session.post(url)
    
    if not response.ok:
        handle_graph_error(response)
        
    upload_url = response.json().get('uploadUrl')
    if not upload_url:
        raise DriveError("Failed to create upload session")
    
    # Upload file in chunks
    chunk_size = 327680  # 320 KB chunks
    for start in range(0, content_size, chunk_size):
        chunk_end = min(start + chunk_size, content_size)
        content_range = f"bytes {start}-{chunk_end-1}/{content_size}"
        
        if hasattr(content, 'read'):
            chunk = content.read(chunk_size)
        else:
            chunk = content[start:chunk_end]
            
        headers = {"Content-Range": content_range}
        response = session.put(upload_url, data=chunk, headers=headers)
        
        if not response.ok and response.status_code != 202:  # 202 means chunk accepted
            handle_graph_error(response)
            
    return format_drive_item(response.json())

def download_file(current_user: str, item_id: str, access_token: str) -> bytes:
    """
    Downloads a file from OneDrive.
    
    Args:
        current_user: User identifier
        item_id: ID of the file to download
    
    Returns:
        File content as bytes
    
    Raises:
        ItemNotFoundError: If file doesn't exist
        DriveError: For other download failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/content"
        response = session.get(url)
        
        if not response.ok:
            handle_graph_error(response)
            
        return response.content
        
    except requests.RequestException as e:
        raise DriveError(f"Network error while downloading file: {str(e)}")

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

def format_drive_item(item: Dict) -> Dict:
    """
    Format drive item data consistently.
    
    Args:
        item: Raw drive item data from Graph API
    
    Returns:
        Dict containing formatted item details
    """
    return {
        'id': item['id'],
        'name': item.get('name', ''),
        'size': item.get('size', 0),
        'webUrl': item.get('webUrl', ''),
        'createdDateTime': item.get('createdDateTime', ''),
        'lastModifiedDateTime': item.get('lastModifiedDateTime', ''),
        'type': 'folder' if item.get('folder') else 'file',
        'mimeType': item.get('file', {}).get('mimeType', '') if item.get('file') else '',
        'parentReference': item.get('parentReference', {}),
        'shared': bool(item.get('shared')),
        'createdBy': item.get('createdBy', {}).get('user', {}).get('displayName', ''),
        'lastModifiedBy': item.get('lastModifiedBy', {}).get('user', {}).get('displayName', '')
    }


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


def create_folder(current_user: str, folder_name: str, parent_folder_id: str = "root", access_token: str = None) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{parent_folder_id}/children"
        payload = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename"
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_drive_item(response.json())
    except requests.RequestException as e:
        raise DriveError(f"Network error while creating folder: {str(e)}")


def update_drive_item(current_user: str, item_id: str, updates: Dict, access_token: str = None) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}"
        response = session.patch(url, json=updates)
        if not response.ok:
            handle_graph_error(response)
        return format_drive_item(response.json())
    except requests.RequestException as e:
        raise DriveError(f"Network error while updating drive item: {str(e)}")


def copy_drive_item(current_user: str, item_id: str, new_name: str, parent_folder_id: str = "root", access_token: str = None) -> Dict:
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


def move_drive_item(current_user: str, item_id: str, new_parent_id: str, access_token: str = None) -> Dict:
    try:
        updates = {"parentReference": {"id": new_parent_id}}
        return update_drive_item(current_user, item_id, updates, access_token)
    except requests.RequestException as e:
        raise DriveError(f"Network error while moving drive item: {str(e)}")


def create_sharing_link(current_user: str, item_id: str, link_type: str = "view", scope: str = "anonymous", access_token: str = None) -> Dict:
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


def invite_to_drive_item(current_user: str, item_id: str, recipients: List[Dict], message: str = "", require_sign_in: bool = True, send_invitation: bool = True, roles: Optional[List[str]] = None, access_token: str = None) -> Dict:
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
            "roles": roles
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise DriveError(f"Network error while inviting to drive item: {str(e)}")