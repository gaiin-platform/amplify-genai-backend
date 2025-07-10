import json
import requests
from typing import Dict, List, Optional
import base64
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_word"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class WordDocError(Exception):
    """Base exception for Word document operations"""

    pass


class DocumentNotFoundError(WordDocError):
    """Raised when a document cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        raise DocumentNotFoundError("Document not found")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise WordDocError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def format_document(doc: Dict) -> Dict:
    """
    Format document data consistently.

    Args:
        doc: Raw document data from Graph API

    Returns:
        Dict containing formatted document details
    """
    return {
        "id": doc.get("id", ""),
        "name": doc.get("name", ""),
        "webUrl": doc.get("webUrl", ""),
        "createdDateTime": doc.get("createdDateTime", ""),
        "lastModifiedDateTime": doc.get("lastModifiedDateTime", ""),
        "size": doc.get("size", 0),
        "createdBy": doc.get("createdBy", {}).get("user", {}).get("displayName", ""),
        "lastModifiedBy": doc.get("lastModifiedBy", {})
        .get("user", {})
        .get("displayName", ""),
    }


def create_document(
    current_user: str,
    name: str,
    content: str = "",
    folder_path: str = None,
    access_token: str = None,
) -> Dict:
    """
    Create a new Word document.

    Args:
        current_user: The user's identifier
        name: Name of the document (must end in .docx)
        content: Optional initial content
        folder_path: Optional OneDrive folder path

    Returns:
        Created document details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        if not name.endswith(".docx"):
            name += ".docx"

        url = f"{GRAPH_ENDPOINT}/me/drive/root:/{'/' + folder_path + '/' if folder_path else '/'}{name}:/content"

        # Create empty document or with initial content
        headers = {
            "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }
        response = session.put(url, data=content, headers=headers)

        if not response.ok:
            handle_graph_error(response)

        return format_document(response.json())

    except requests.RequestException as e:
        raise WordDocError(f"Network error while creating document: {str(e)}")


def get_document_content(
    current_user: str, document_id: str, access_token: str = None
) -> bytes:
    """
    Get the content of a Word document.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        Document content as bytes
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        return response.content

    except requests.RequestException as e:
        raise WordDocError(f"Network error while fetching document content: {str(e)}")


def update_document_content(
    current_user: str, document_id: str, content: bytes, access_token: str = None
) -> Dict:
    """
    Update the content of a Word document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        content: New content as bytes

    Returns:
        Updated document details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content"

        headers = {
            "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }
        response = session.put(url, data=content, headers=headers)

        if not response.ok:
            handle_graph_error(response)

        return format_document(response.json())

    except requests.RequestException as e:
        raise WordDocError(f"Network error while updating document: {str(e)}")


def delete_document(
    current_user: str, document_id: str, access_token: str = None
) -> Dict:
    """
    Delete a Word document.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        Status dictionary
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}"

        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "id": document_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise WordDocError(f"Network error while deleting document: {str(e)}")


def list_documents(
    current_user: str, folder_path: str = None, access_token: str = None
) -> List[Dict]:
    """
    List Word documents in a folder or root.

    Args:
        current_user: The user's identifier
        folder_path: Optional folder path

    Returns:
        List of document details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        if folder_path:
            url = f"{GRAPH_ENDPOINT}/me/drive/root:/{folder_path}:/children"
        else:
            url = f"{GRAPH_ENDPOINT}/me/drive/root/children"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        items = response.json().get("value", [])
        # Filter for Word documents only
        docs = [item for item in items if item.get("name", "").endswith(".docx")]
        return [format_document(doc) for doc in docs]

    except requests.RequestException as e:
        raise WordDocError(f"Network error while listing documents: {str(e)}")


def share_document(
    current_user: str,
    document_id: str,
    user_email: str,
    permission_level: str = "read",
    access_token: str = None,
) -> Dict:
    """
    Share a Word document with another user.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        user_email: Email of the user to share with
        permission_level: 'read' or 'write'

    Returns:
        Sharing link and permissions
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/invite"

        body = {
            "recipients": [{"email": user_email}],
            "roles": ["read"] if permission_level == "read" else ["write"],
            "requireSignIn": True,
            "sendInvitation": True,
        }

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while sharing document: {str(e)}")


def get_document_permissions(
    current_user: str, document_id: str, access_token: str = None
) -> List[Dict]:
    """
    Get sharing permissions for a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        List of permission details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/permissions"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        return response.json().get("value", [])

    except requests.RequestException as e:
        raise WordDocError(f"Network error while fetching permissions: {str(e)}")


def remove_permission(
    current_user: str, document_id: str, permission_id: str, access_token: str = None
) -> Dict:
    """
    Remove a sharing permission from a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        permission_id: Permission ID to remove

    Returns:
        Status dictionary
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = (
            f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/permissions/{permission_id}"
        )

        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "id": permission_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise WordDocError(f"Network error while removing permission: {str(e)}")


def get_document_versions(
    current_user: str, document_id: str, access_token: str = None
) -> List[Dict]:
    """
    Get version history of a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        List of version details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/versions"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        versions = response.json().get("value", [])
        return [
            {
                "id": version.get("id"),
                "lastModifiedDateTime": version.get("lastModifiedDateTime"),
                "size": version.get("size"),
                "modifiedBy": version.get("lastModifiedBy", {})
                .get("user", {})
                .get("displayName"),
            }
            for version in versions
        ]

    except requests.RequestException as e:
        raise WordDocError(f"Network error while fetching versions: {str(e)}")


def restore_version(
    current_user: str, document_id: str, version_id: str, access_token: str = None
) -> Dict:
    """
    Restore a previous version of a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        version_id: Version ID to restore

    Returns:
        Restored document details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/versions/{version_id}/restoreVersion"

        response = session.post(url)
        if not response.ok:
            handle_graph_error(response)

        # Get updated document details
        doc_response = session.get(f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}")
        if not doc_response.ok:
            handle_graph_error(doc_response)

        return format_document(doc_response.json())

    except requests.RequestException as e:
        raise WordDocError(f"Network error while restoring version: {str(e)}")


def add_comment(
    current_user: str,
    document_id: str,
    text: str,
    content_range: Dict,
    access_token: str = None,
) -> Dict:
    """
    Add a comment to a specific range in a Word document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        text: Comment text
        content_range: Dictionary specifying the range to comment on (e.g., {"start": 0, "length": 10})

    Returns:
        Comment details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/comments"

        body = {"content": text, "contentRange": content_range}

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while adding comment: {str(e)}")


def get_document_statistics(
    current_user: str, document_id: str, access_token: str = None
) -> Dict:
    """
    Get document statistics including word count, page count, etc.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        Dictionary containing document statistics
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/workbook/application/calculate"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        return {
            "wordCount": response.json().get("wordCount", 0),
            "pageCount": response.json().get("pageCount", 0),
            "characterCount": response.json().get("characterCount", 0),
            "paragraphCount": response.json().get("paragraphCount", 0),
        }

    except requests.RequestException as e:
        raise WordDocError(f"Network error while getting statistics: {str(e)}")


def search_document(
    current_user: str, document_id: str, search_text: str, access_token: str = None
) -> List[Dict]:
    """
    Search for text within a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        search_text: Text to search for

    Returns:
        List of search results with locations
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/search(q='{search_text}')"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        return response.json().get("value", [])

    except requests.RequestException as e:
        raise WordDocError(f"Network error while searching document: {str(e)}")


def apply_formatting(
    current_user: str,
    document_id: str,
    format_range: Dict,
    formatting: Dict,
    access_token: str = None,
) -> Dict:
    """
    Apply formatting to a specific range in the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        format_range: Dictionary specifying the range to format
        formatting: Dictionary containing formatting options (font, size, color, etc.)

    Returns:
        Updated range details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/ranges/format"

        body = {"range": format_range, "format": formatting}

        response = session.patch(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while applying formatting: {str(e)}")


def get_document_sections(
    current_user: str, document_id: str, access_token: str = None
) -> List[Dict]:
    """
    Get all sections/paragraphs in a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID

    Returns:
        List of document sections with their content
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/paragraphs"

        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        return response.json().get("value", [])

    except requests.RequestException as e:
        raise WordDocError(f"Network error while getting sections: {str(e)}")


def insert_section(
    current_user: str,
    document_id: str,
    content: str,
    position: int = None,
    access_token: str = None,
) -> Dict:
    """
    Insert a new section/paragraph at a specific position in the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        content: Content to insert
        position: Optional position to insert at (None for end of document)

    Returns:
        Inserted section details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/insertParagraph"

        body = {
            "content": content,
            "position": position if position is not None else "End",
        }

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while inserting section: {str(e)}")


def replace_text(
    current_user: str,
    document_id: str,
    search_text: str,
    replace_text: str,
    access_token: str = None,
) -> Dict:
    """
    Replace all occurrences of text in a document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        search_text: Text to find
        replace_text: Text to replace with

    Returns:
        Dictionary with replacement details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/replaceAll"

        body = {"searchText": search_text, "replaceText": replace_text}

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while replacing text: {str(e)}")


def create_table(
    current_user: str,
    document_id: str,
    rows: int,
    columns: int,
    position: Dict = None,
    access_token: str = None,
) -> Dict:
    """
    Insert a new table into the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        rows: Number of rows
        columns: Number of columns
        position: Optional position to insert table (Dict with start/end)

    Returns:
        Created table details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/insertTable"

        body = {"rows": rows, "columns": columns}
        if position:
            body["position"] = position

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while creating table: {str(e)}")


def update_table_cell(
    current_user: str,
    document_id: str,
    table_id: str,
    row: int,
    column: int,
    content: str,
    formatting: Dict = None,
    access_token: str = None,
) -> Dict:
    """
    Update content and formatting of a table cell.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        table_id: ID of the table
        row: Row index (0-based)
        column: Column index (0-based)
        content: Cell content
        formatting: Optional cell formatting

    Returns:
        Updated cell details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/tables/{table_id}/cell"

        body = {"row": row, "column": column, "content": content}
        if formatting:
            body["format"] = formatting

        response = session.patch(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while updating table cell: {str(e)}")


def create_list(
    current_user: str,
    document_id: str,
    items: List[str],
    list_type: str = "bullet",
    position: Dict = None,
    access_token: str = None,
) -> Dict:
    """
    Create a bulleted or numbered list in the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        items: List of items to add
        list_type: "bullet" or "number"
        position: Optional position to insert list

    Returns:
        Created list details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/insertList"

        body = {"items": items, "listType": list_type}
        if position:
            body["position"] = position

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while creating list: {str(e)}")


def insert_page_break(
    current_user: str, document_id: str, position: Dict = None, access_token: str = None
) -> Dict:
    """
    Insert a page break at the specified position.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        position: Optional position to insert page break

    Returns:
        Operation status
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/insertPageBreak"

        body = {}
        if position:
            body["position"] = position

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while inserting page break: {str(e)}")


def set_header_footer(
    current_user: str,
    document_id: str,
    content: str,
    is_header: bool = True,
    access_token: str = None,
) -> Dict:
    """
    Set the header or footer content for the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        content: Content to set
        is_header: True for header, False for footer

    Returns:
        Updated header/footer details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        section = "header" if is_header else "footer"
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/sections/0/{section}"

        body = {"content": content}

        response = session.patch(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while setting {section}: {str(e)}")


def insert_image(
    current_user: str,
    document_id: str,
    image_data: bytes,
    position: Dict = None,
    name: str = None,
    access_token: str = None,
) -> Dict:
    """
    Insert an image into the document.

    Args:
        current_user: The user's identifier
        document_id: Document ID
        image_data: Image bytes
        position: Optional position to insert image
        name: Optional image name

    Returns:
        Inserted image details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{document_id}/content/body/insertImage"

        # Convert image to base64
        image_b64 = base64.b64encode(image_data).decode("utf-8")

        body = {"image": image_b64}
        if position:
            body["position"] = position
        if name:
            body["name"] = name

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise WordDocError(f"Network error while inserting image: {str(e)}")
