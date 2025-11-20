import base64
import json
import uuid
from datetime import datetime, timezone
from typing import BinaryIO, Dict, List, Optional, Union
from urllib.parse import unquote

import requests
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_onenote"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class OneNoteError(Exception):
    """Base exception for OneNote operations"""

    pass


class NotebookNotFoundError(OneNoteError):
    """Raised when notebook cannot be found"""

    pass


class SectionNotFoundError(OneNoteError):
    """Raised when section cannot be found"""

    pass


class PageNotFoundError(OneNoteError):
    """Raised when page cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "notebook" in error_message:
            raise NotebookNotFoundError("Notebook not found")
        elif "section" in error_message:
            raise SectionNotFoundError("Section not found")
        elif "page" in error_message:
            raise PageNotFoundError("Page not found")
        raise OneNoteError("Resource not found")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise OneNoteError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_notebooks(
    current_user: str, top: int = 10, access_token: str = None
) -> List[Dict]:
    """
    Lists user's OneNote notebooks with pagination support.

    Args:
        current_user: User identifier
        top: Maximum number of notebooks to retrieve

    Returns:
        List of notebook details

    Raises:
        OneNoteError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/onenote/notebooks?$top={top}"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        notebooks = response.json().get("value", [])
        return [format_notebook(notebook) for notebook in notebooks]

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while listing notebooks: {str(e)}")


def list_sections_in_notebook(
    current_user: str, notebook_id: str, access_token: str
) -> List[Dict]:
    """
    Lists sections in a notebook.

    Args:
        current_user: User identifier
        notebook_id: Notebook ID

    Returns:
        List of section details

    Raises:
        NotebookNotFoundError: If notebook doesn't exist
        OneNoteError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/onenote/notebooks/{notebook_id}/sections"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        sections = response.json().get("value", [])
        return [format_section(section) for section in sections]

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while listing sections: {str(e)}")


def list_pages_in_section(
    current_user: str, section_id: str, access_token: str
) -> List[Dict]:
    """
    Lists pages in a section.

    Args:
        current_user: User identifier
        section_id: Section ID

    Returns:
        List of page details

    Raises:
        SectionNotFoundError: If section doesn't exist
        OneNoteError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/onenote/sections/{section_id}/pages"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        pages = response.json().get("value", [])
        return [format_page(page) for page in pages]

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while listing pages: {str(e)}")


def create_page_in_section(
    current_user: str, section_id: str, title: str, html_content: str, access_token: str
) -> Dict:
    """
    Creates a new page in a section.

    Args:
        current_user: User identifier
        section_id: Section ID
        title: Page title
        html_content: HTML content for the page

    Returns:
        Dict containing created page details

    Raises:
        SectionNotFoundError: If section doesn't exist
        OneNoteError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/onenote/sections/{section_id}/pages"

        # Validate inputs
        if not title:
            raise OneNoteError("Title cannot be empty")

        headers = session.headers.copy()
        headers["Content-Type"] = "application/xhtml+xml"

        page_html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta name="created" content="{_format_created_time()}"/>
</head>
<body>
{html_content}
</body>
</html>"""

        response = session.post(url, data=page_html.encode("utf-8"), headers=headers)

        if not response.ok:
            handle_graph_error(response)

        return format_page(response.json())

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while creating page: {str(e)}")


def get_page_content(current_user: str, page_id: str, access_token: str) -> str:
    """
    Retrieves the HTML content of a page.

    Args:
        current_user: User identifier
        page_id: Page ID

    Returns:
        Page content as HTML string

    Raises:
        PageNotFoundError: If page doesn't exist
        OneNoteError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/onenote/pages/{page_id}/content"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return response.text

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while getting page content: {str(e)}")


def _sanitize_block_name(name: str) -> str:
    """
    Sanitizes a filename to create a valid block name for OneNote.
    Removes extension and replaces invalid characters with underscores.
    """
    import re

    # Remove file extension
    name_without_ext = name.rsplit('.', 1)[0] if '.' in name else name
    # Replace invalid characters with underscores and ensure it starts with a letter
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name_without_ext)
    # Ensure it starts with a letter (prepend 'img_' if it starts with a number)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"img_{sanitized}"
    # Fallback if empty or invalid
    return sanitized if sanitized else "image_block"


def _decode_content(content: Union[bytes, str]) -> bytes:
    """Helper to decode base64/URL encoded content to bytes."""
    if isinstance(content, str):
        try:
            # URL decode first, then base64 decode
            decoded_content = unquote(content)
            # Add padding if needed for proper base64 decoding
            missing_padding = len(decoded_content) % 4
            if missing_padding:
                decoded_content += '=' * (4 - missing_padding)
            return base64.b64decode(decoded_content)
        except Exception as e:
            raise OneNoteError(f"Failed to decode base64 content: {e}")
    return content


def _build_html_part(boundary: str, title: str, html_body: str) -> bytearray:
    """Build HTML part of multipart payload."""
    payload = bytearray()

    # HTML part
    payload.extend(f"--{boundary}\r\n".encode("utf-8"))
    payload.extend(b'Content-Disposition: form-data; name="Presentation"\r\n')
    payload.extend(b"Content-Type: text/html\r\n\r\n")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta name="created" content="{_format_created_time()}"/>
</head>
<body>
    {html_body}
</body>
</html>"""

    payload.extend(html_content.encode("utf-8"))
    payload.extend(b"\r\n")

    return payload


def _build_file_part(boundary: str, block_name: str, file_content: Union[bytes, str],
                    file_content_type: str) -> bytearray:
    """Build file attachment part of multipart payload."""
    payload = bytearray()

    # File part
    payload.extend(f"--{boundary}\r\n".encode("utf-8"))
    payload.extend(f'Content-Disposition: form-data; name="{block_name}"\r\n'.encode("utf-8"))
    payload.extend(f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"))

    if hasattr(file_content, "read"):
        payload.extend(file_content.read())
    else:
        payload.extend(_decode_content(file_content))
    payload.extend(b"\r\n")

    return payload


def create_page_with_attachment(
    current_user: str,
    section_id: str,
    title: str,
    html_body: str,
    file_name: str,
    file_content: Union[bytes, str, BinaryIO],
    file_content_type: str,
    access_token: str,
) -> Dict:
    """
    Creates a page with a file attachment.

    Args:
        current_user: User identifier
        section_id: Section ID
        title: Page title
        html_body: HTML content
        file_name: Name of the attachment
        file_content: File content as bytes, base64 string, or file object
        file_content_type: File MIME type

    Returns:
        Dict containing created page details

    Raises:
        SectionNotFoundError: If section doesn't exist
        OneNoteError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Input validation
        if not title:
            raise OneNoteError("Title cannot be empty")
        if not file_content:
            raise OneNoteError("File content is required")

        # Create multipart form data
        boundary = f"----OneNoteFormBoundary{uuid.uuid4()}"
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

        # Build payload using helper functions
        payload = bytearray()

        # Create dynamic block name from file name
        file_block_name = _sanitize_block_name(file_name)

        # Add HTML part with proper attachment reference
        # Check for filename match first (more specific), then block name match
        if f'name:{file_name}' in html_body:
            # Handle case where HTML uses full filename, replace with sanitized name
            html_with_attachment = html_body.replace(f'name:{file_name}', f'name:{file_block_name}')
        elif f'name:{file_block_name}' in html_body and f'name:{file_name}' not in html_body:
            # Use HTML as-is only if it has the block name but NOT the filename
            html_with_attachment = html_body
        elif 'name:fileBlock1' in html_body:
            # Legacy support: replace fileBlock1 with dynamic name
            html_with_attachment = html_body.replace('name:fileBlock1', f'name:{file_block_name}')
        else:
            # Append attachment automatically for backward compatibility
            html_with_attachment = f"{html_body}<p>Attached file: <object data-attachment='{file_name}' data='name:{file_block_name}' type='{file_content_type}' /></p>"

        payload.extend(_build_html_part(boundary, title, html_with_attachment))

        # Add file part with dynamic block name
        payload.extend(_build_file_part(boundary, file_block_name, file_content, file_content_type))

        # Close boundary
        payload.extend(f"--{boundary}--\r\n".encode("utf-8"))

        url = f"{GRAPH_ENDPOINT}/me/onenote/sections/{section_id}/pages"
        response = session.post(url, headers=headers, data=payload)

        if not response.ok:
            handle_graph_error(response)

        return format_page(response.json())

    except requests.RequestException as e:
        raise OneNoteError(f"Network error while creating page: {str(e)}")

def _format_created_time() -> str:
    """Returns ISO8601 formatted current time"""
    return datetime.now(timezone.utc).isoformat()


def format_notebook(notebook: Dict) -> Dict:
    """Format notebook data consistently"""
    return {
        "id": notebook["id"],
        "displayName": notebook.get("displayName", ""),
        "createdBy": notebook.get("createdBy", {})
        .get("user", {})
        .get("displayName", ""),
        "lastModifiedBy": notebook.get("lastModifiedBy", {})
        .get("user", {})
        .get("displayName", ""),
        "createdDateTime": notebook.get("createdDateTime", ""),
        "lastModifiedDateTime": notebook.get("lastModifiedDateTime", ""),
        "links": notebook.get("links", {}),
    }


def format_section(section: Dict) -> Dict:
    """Format section data consistently"""
    return {
        "id": section["id"],
        "displayName": section.get("displayName", ""),
        "createdBy": section.get("createdBy", {})
        .get("user", {})
        .get("displayName", ""),
        "lastModifiedBy": section.get("lastModifiedBy", {})
        .get("user", {})
        .get("displayName", ""),
        "pagesUrl": section.get("pagesUrl", ""),
    }


def format_page(page: Dict) -> Dict:
    """Format page data consistently"""
    return {
        "id": page["id"],
        "title": page.get("title", ""),
        "createdBy": page.get("createdBy", {}).get("user", {}).get("displayName", ""),
        "lastModifiedBy": page.get("lastModifiedBy", {})
        .get("user", {})
        .get("displayName", ""),
        "createdDateTime": page.get("createdDateTime", ""),
        "lastModifiedDateTime": page.get("lastModifiedDateTime", ""),
        "contentUrl": page.get("contentUrl", ""),
        "links": page.get("links", {}),
    }
