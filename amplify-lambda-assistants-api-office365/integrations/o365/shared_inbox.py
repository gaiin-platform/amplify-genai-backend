import json
import re
import requests
from typing import Dict, List, Optional
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_exchange"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


def _html_to_plain_text(html: str) -> str:
    """Strip HTML tags and decode common entities to produce clean plain text."""
    html = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<(br|p|div|tr|li|h[1-6])\b[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<') \
               .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    html = re.sub(r'\n[ \t]+', '\n', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


from pycommon.logger import getLogger
logger = getLogger(integration_name)


class SharedInboxError(Exception):
    """Base exception for shared mailbox operations."""
    pass


class SharedMessageNotFoundError(SharedInboxError):
    """Raised when a message cannot be found in the shared mailbox."""
    pass


def _handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses."""
    if response.status_code == 404:
        raise SharedMessageNotFoundError("Resource not found in shared mailbox")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
        error_code = error_data.get("error", {}).get("code", "")
        logger.error("Graph API error response (code=%s, status=%s): %s", error_code, response.status_code, error_data)
    except json.JSONDecodeError:
        error_message = response.text
        error_code = ""
        logger.error("Graph API error (non-JSON): %s", response.text)

    # Provide a more helpful message for 403 errors on shared mailbox access
    if response.status_code == 403:
        raise SharedInboxError(
            f"Access denied to shared mailbox. Ensure the user has been granted "
            f"Full Access to this mailbox in Exchange Admin Center, and that "
            f"Mail.Read.Shared permission has been admin-consented on the app registration. "
            f"(Graph API code: {error_code}, message: {error_message})"
        )

    raise SharedInboxError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def _format_message(msg: Dict, include_body: bool = False) -> Dict:
    """Format a Graph API message object into a clean dict."""
    body_content = None
    if include_body:
        raw_body = msg.get("body", {})
        content_type = raw_body.get("contentType", "text")
        content = raw_body.get("content", "")
        body_content = _html_to_plain_text(content) if content_type == "html" else content

    return {
        "id": msg.get("id"),
        "subject": msg.get("subject"),
        "from": msg.get("from", {}).get("emailAddress", {}),
        "toRecipients": [r.get("emailAddress", {}) for r in msg.get("toRecipients", [])],
        "ccRecipients": [r.get("emailAddress", {}) for r in msg.get("ccRecipients", [])],
        "receivedDateTime": msg.get("receivedDateTime"),
        "isRead": msg.get("isRead"),
        "isDraft": msg.get("isDraft"),
        "hasAttachments": msg.get("hasAttachments"),
        "importance": msg.get("importance"),
        "conversationId": msg.get("conversationId"),
        "bodyPreview": msg.get("bodyPreview"),
        "body": body_content,
    }


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def list_shared_mailbox_messages(
    current_user: str,
    mailbox_email: str,
    folder_id: str = "Inbox",
    top: int = 10,
    skip: int = 0,
    filter_query: Optional[str] = None,
    include_body: bool = False,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists messages in a specified folder of a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier (used to look up the microsoft_exchange OAuth token)
        mailbox_email: Email address of the shared mailbox (e.g. support@example.com)
        folder_id: Folder ID or well-known name (default: "Inbox")
        top: Maximum number of messages to retrieve (1-100)
        skip: Number of messages to skip for pagination
        filter_query: Optional OData filter query
        include_body: Whether to include full message body (default: False)
        access_token: Amplify API access token

    Returns:
        List of message summary dicts
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders/{folder_id}/messages"

        base_select = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,hasAttachments,importance,isDraft,isRead,conversationId,bodyPreview"
        select_fields = f"{base_select},body" if include_body else base_select

        if filter_query:
            params = {
                "$filter": filter_query,
                "$top": top,
                "$select": select_fields,
            }
        else:
            params = {
                "$top": top,
                "$skip": skip,
                "$select": select_fields,
                "$orderby": "receivedDateTime desc",
            }

        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        messages = response.json().get("value", [])
        return [_format_message(msg, include_body=include_body) for msg in messages]

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error listing shared mailbox messages: {str(e)}")


def get_shared_mailbox_message(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    include_body: bool = True,
    access_token: str = None,
) -> Dict:
    """
    Gets detailed information about a specific message in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID
        include_body: Whether to include the full message body (default: True)
        access_token: Amplify API access token

    Returns:
        Dict with full message details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}"

        if include_body:
            select_fields = "id,subject,body,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,importance,isRead,isDraft,conversationId,bodyPreview"
        else:
            select_fields = "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,importance,isRead,isDraft,conversationId,bodyPreview"

        params = {"$select": select_fields}
        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        return _format_message(response.json(), include_body=include_body)

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error fetching shared mailbox message: {str(e)}")


def get_shared_mailbox_attachments(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists attachments for a specific message in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID
        access_token: Amplify API access token

    Returns:
        List of attachment metadata dicts (id, name, contentType, size, isInline)
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/attachments"
        params = {"$select": "id,name,contentType,size,isInline,lastModifiedDateTime"}
        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        attachments = response.json().get("value", [])
        return [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "contentType": a.get("contentType"),
                "size": a.get("size"),
                "isInline": a.get("isInline", False),
                "lastModifiedDateTime": a.get("lastModifiedDateTime"),
            }
            for a in attachments
        ]

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error fetching shared mailbox attachments: {str(e)}")


def search_shared_mailbox_messages(
    current_user: str,
    mailbox_email: str,
    search_query: str,
    top: int = 10,
    include_body: bool = False,
    access_token: str = None,
) -> List[Dict]:
    """
    Searches messages in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        search_query: Search query string (KQL or simple text)
        top: Maximum number of results (1-100)
        include_body: Whether to include message body in results (default: False)
        access_token: Amplify API access token

    Returns:
        List of matching message summary dicts
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages"

        base_select = "id,subject,from,toRecipients,receivedDateTime,hasAttachments,isRead,conversationId,bodyPreview"
        select_fields = f"{base_select},body" if include_body else base_select

        params = {
            "$search": f'"{search_query}"',
            "$top": top,
            "$select": select_fields,
        }

        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        messages = response.json().get("value", [])
        return [_format_message(msg, include_body=include_body) for msg in messages]

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error searching shared mailbox: {str(e)}")


def list_shared_mailbox_folders(
    current_user: str,
    mailbox_email: str,
    include_child_folders: bool = True,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists mail folders in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        include_child_folders: Whether to recursively include child folders (default: True)
        access_token: Amplify API access token

    Returns:
        List of folder dicts (id, displayName, totalItemCount, unreadItemCount, parentFolderId)
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders"
        params = {
            "$select": "id,displayName,totalItemCount,unreadItemCount,parentFolderId",
            "$top": 100,
        }
        if include_child_folders:
            params["includeHiddenFolders"] = "false"

        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        folders = response.json().get("value", [])
        result = [
            {
                "id": f.get("id"),
                "displayName": f.get("displayName"),
                "totalItemCount": f.get("totalItemCount"),
                "unreadItemCount": f.get("unreadItemCount"),
                "parentFolderId": f.get("parentFolderId"),
            }
            for f in folders
        ]

        if include_child_folders:
            child_results = []
            for folder in folders:
                child_url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders/{folder['id']}/childFolders"
                child_response = session.get(child_url, params={"$select": "id,displayName,totalItemCount,unreadItemCount,parentFolderId", "$top": 100})
                if child_response.ok:
                    for child in child_response.json().get("value", []):
                        child_results.append({
                            "id": child.get("id"),
                            "displayName": child.get("displayName"),
                            "totalItemCount": child.get("totalItemCount"),
                            "unreadItemCount": child.get("unreadItemCount"),
                            "parentFolderId": child.get("parentFolderId"),
                        })
            result.extend(child_results)

        return result

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error listing shared mailbox folders: {str(e)}")


def create_shared_mailbox_draft(
    current_user: str,
    mailbox_email: str,
    subject: str,
    body: str,
    to_recipients: Optional[List[str]] = None,
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    content_type: str = "text",
    access_token: str = None,
) -> Dict:
    """
    Creates a draft message in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox (e.g. support@example.com)
        subject: Draft email subject
        body: Draft email body content
        to_recipients: Optional list of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Importance level ('low', 'normal', 'high')
        content_type: Content type ('text' or 'html')
        access_token: Amplify API access token

    Returns:
        Dict containing the draft message id

    Raises:
        SharedInboxError: If creation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages"

        payload: Dict = {
            "subject": subject,
            "body": {"contentType": content_type, "content": body},
            "importance": importance,
            "isDraft": True,
        }
        if to_recipients:
            payload["toRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in to_recipients
            ]
        if cc_recipients:
            payload["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc_recipients
            ]
        if bcc_recipients:
            payload["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc_recipients
            ]

        response = session.post(url, json=payload)
        if not response.ok:
            _handle_graph_error(response)

        response_data = response.json()
        return {
            "message_id": response_data.get("id"),
            "subject": response_data.get("subject"),
            "isDraft": response_data.get("isDraft"),
            "createdDateTime": response_data.get("createdDateTime"),
        }

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error creating shared mailbox draft: {str(e)}")
