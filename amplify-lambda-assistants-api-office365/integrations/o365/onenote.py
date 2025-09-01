import json
import uuid
import requests
from typing import Dict, List, Optional, Union, BinaryIO
from integrations.oauth import get_ms_graph_session
from datetime import datetime


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


def create_page_with_image_and_attachment(
    current_user: str,
    section_id: str,
    title: str,
    html_body: str,
    image_name: str,
    image_content: Union[bytes, BinaryIO],
    image_content_type: str,
    file_name: str,
    file_content: Union[bytes, BinaryIO],
    file_content_type: str,
    access_token: str,
) -> Dict:
    """
    Creates a page with embedded image and file attachment.

    Args:
        current_user: User identifier
        section_id: Section ID
        title: Page title
        html_body: HTML content
        image_name: Name of the image file
        image_content: Image content as bytes or file object
        image_content_type: Image MIME type
        file_name: Name of the attachment
        file_content: File content as bytes or file object
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

        if not image_content:
            raise OneNoteError("Image content is required")

        if not file_content:
            raise OneNoteError("File content is required")

        # Create multipart form data
        boundary = f"----OneNoteFormBoundary{uuid.uuid4()}"
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

        # Build payload
        payload = bytearray()

        # HTML part
        payload.extend(f"--{boundary}\r\n".encode("utf-8"))
        payload.extend(b"Content-Type: text/html; charset=utf-8\r\n\r\n")

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta name="created" content="{_format_created_time()}"/>
</head>
<body>
    <h1>{title}</h1>
    <p>{html_body}</p>
    <p>Here is an embedded image:</p>
    <img src="name:imageBlock1" alt="EmbeddedImage" />
    <p>Here is an attached file:</p>
    <object data-attachment="{file_name}" data="name:fileBlock1" type="{file_content_type}" />
</body>
</html>"""

        payload.extend(html_content.encode("utf-8"))
        payload.extend(b"\r\n")

        # Image part
        payload.extend(f"--{boundary}\r\n".encode("utf-8"))
        payload.extend(
            f'Content-Disposition: form-data; name="imageBlock1"\r\n'.encode("utf-8")
        )
        payload.extend(f"Content-Type: {image_content_type}\r\n\r\n".encode("utf-8"))
        if hasattr(image_content, "read"):
            payload.extend(image_content.read())
        else:
            payload.extend(image_content)
        payload.extend(b"\r\n")

        # File part
        payload.extend(f"--{boundary}\r\n".encode("utf-8"))
        payload.extend(
            f'Content-Disposition: form-data; name="fileBlock1"\r\n'.encode("utf-8")
        )
        payload.extend(f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"))
        if hasattr(file_content, "read"):
            payload.extend(file_content.read())
        else:
            payload.extend(file_content)
        payload.extend(b"\r\n")

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
    return datetime.utcnow().isoformat()


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
