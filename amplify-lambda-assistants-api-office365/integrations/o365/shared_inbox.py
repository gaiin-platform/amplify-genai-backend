import base64
import json
import requests
from typing import Dict, List, Optional
from integrations.oauth import get_ms_graph_session
from integrations.o365.html_utils import html_to_plain_text

integration_name = "microsoft_exchange"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"

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
        body_content = html_to_plain_text(content) if content_type == "html" else content

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


def download_shared_mailbox_attachment(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    attachment_id: str,
    access_token: str = None,
) -> Dict:
    """
    Downloads a specific attachment from a message in a shared Exchange mailbox.

    For files under 7MB, returns base64-encoded content directly in the response.
    For larger files, returns a temporary download URL to avoid API Gateway limits
    (API Gateway has a 10MB response limit; base64 encoding adds ~33% overhead,
    so 7MB is the safe threshold).

    Handles three Graph API attachment types:
    - fileAttachment: Returns base64 contentBytes or a download URL
    - itemAttachment: Returns guidance (embedded Outlook items require special handling)
    - referenceAttachment: Returns the sourceUrl to the cloud-stored file

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID
        attachment_id: Graph API attachment ID (from list_shared_mailbox_attachments)
        access_token: Amplify API access token

    Returns:
        Dict with attachment content or download URL and metadata

    Raises:
        SharedInboxError: If the attachment cannot be fetched
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        metadata_url = (
            f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/attachments/{attachment_id}"
        )
        metadata_response = session.get(metadata_url)

        if not metadata_response.ok:
            if metadata_response.status_code == 404:
                raise SharedInboxError("Attachment not found")
            _handle_graph_error(metadata_response)

        attachment_metadata = metadata_response.json()
        attachment_type = attachment_metadata.get("@odata.type")
        file_size = attachment_metadata.get("size", 0)

        # API Gateway has a 10MB response limit; base64 adds ~33% overhead,
        # so anything over 7MB is returned as a download URL instead.
        SIZE_LIMIT_BYTES = 7 * 1024 * 1024  # 7MB

        if attachment_type == "#microsoft.graph.fileAttachment":
            result = {
                "id": attachment_metadata.get("id"),
                "name": attachment_metadata.get("name"),
                "contentType": attachment_metadata.get("contentType"),
                "size": file_size,
                "isInline": attachment_metadata.get("isInline", False),
                "lastModifiedDateTime": attachment_metadata.get("lastModifiedDateTime"),
            }

            if file_size <= SIZE_LIMIT_BYTES:
                # Small file — fetch raw bytes and return as base64
                content_url = (
                    f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}"
                    f"/attachments/{attachment_id}/$value"
                )
                content_response = session.get(content_url)

                if content_response.ok:
                    result["contentBytes"] = base64.b64encode(content_response.content).decode("utf-8")
                    result["deliveryMethod"] = "direct_content"
                else:
                    # Fallback: contentBytes is included in the metadata response for small attachments
                    result["contentBytes"] = attachment_metadata.get("contentBytes")
                    result["deliveryMethod"] = "metadata_content"
            else:
                # Large file — caller must fetch using the download URL with auth headers
                result["downloadUrl"] = (
                    f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}"
                    f"/attachments/{attachment_id}/$value"
                )
                result["deliveryMethod"] = "download_url"
                result["note"] = (
                    f"File too large ({file_size:,} bytes) for direct API response. "
                    f"Use downloadUrl with an Authorization: Bearer header."
                )

            return result

        elif attachment_type == "#microsoft.graph.itemAttachment":
            return {
                "id": attachment_metadata.get("id"),
                "name": attachment_metadata.get("name"),
                "contentType": "application/outlook-item",
                "size": file_size,
                "isInline": False,
                "lastModifiedDateTime": attachment_metadata.get("lastModifiedDateTime"),
                "deliveryMethod": "unsupported",
                "error": "Item attachments (embedded Outlook items) require special handling",
            }

        elif attachment_type == "#microsoft.graph.referenceAttachment":
            return {
                "id": attachment_metadata.get("id"),
                "name": attachment_metadata.get("name"),
                "contentType": "reference/link",
                "isInline": False,
                "sourceUrl": attachment_metadata.get("sourceUrl"),
                "providerType": attachment_metadata.get("providerType"),
                "deliveryMethod": "external_link",
                "note": "Reference attachment — use sourceUrl to access the cloud-stored file",
            }

        else:
            raise SharedInboxError(f"Unsupported attachment type: {attachment_type}")

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error downloading shared mailbox attachment: {str(e)}")


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


def _extract_folder(f: Dict) -> Dict:
    """Return the standard folder dict from a Graph API folder object."""
    return {
        "id": f.get("id"),
        "displayName": f.get("displayName"),
        "totalItemCount": f.get("totalItemCount"),
        "unreadItemCount": f.get("unreadItemCount"),
        "parentFolderId": f.get("parentFolderId"),
    }


def list_shared_mailbox_folders(
    current_user: str,
    mailbox_email: str,
    include_child_folders: bool = True,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists mail folders in a shared Exchange mailbox.

    When include_child_folders is True, child folders are fetched in a single
    request per top-level folder using Graph API's $expand=childFolders, avoiding
    the N+1 request pattern of fetching children individually.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        include_child_folders: Whether to include child folders (default: True)
        access_token: Amplify API access token

    Returns:
        Flat list of folder dicts (id, displayName, totalItemCount, unreadItemCount, parentFolderId)
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders"

        folder_select = "id,displayName,totalItemCount,unreadItemCount,parentFolderId"
        params: Dict = {
            "$select": folder_select,
            "$top": 100,
        }
        if include_child_folders:
            # $expand=childFolders fetches one level of children in the same
            # request, eliminating a separate HTTP call per top-level folder.
            params["$expand"] = f"childFolders($select={folder_select})"

        response = session.get(url, params=params)
        if not response.ok:
            _handle_graph_error(response)

        folders = response.json().get("value", [])
        result = []
        for f in folders:
            result.append(_extract_folder(f))
            if include_child_folders:
                for child in f.get("childFolders", []):
                    result.append(_extract_folder(child))

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


def send_shared_mailbox_mail(
    current_user: str,
    mailbox_email: str,
    subject: str,
    body: str,
    to_recipients: List[str],
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    content_type: str = "text",
    access_token: str = None,
) -> Dict:
    """
    Sends an email from a shared Exchange mailbox.

    Uses POST /users/{mailbox_email}/sendMail so the message is sent with the
    shared mailbox address as the sender (From), rather than the authenticated
    user's personal address.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox (e.g. support@example.com)
        subject: Email subject
        body: Email body content
        to_recipients: List of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Importance level ('low', 'normal', 'high')
        content_type: Content type ('text' or 'html')
        access_token: Amplify API access token

    Returns:
        Dict containing send status

    Raises:
        SharedInboxError: If the send operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/sendMail"

        email_msg: Dict = {
            "message": {
                "subject": subject,
                "body": {"contentType": content_type, "content": body},
                "toRecipients": [
                    {"emailAddress": {"address": addr}} for addr in to_recipients
                ],
                "importance": importance,
            },
            "saveToSentItems": "true",
        }

        if cc_recipients:
            email_msg["message"]["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc_recipients
            ]

        if bcc_recipients:
            email_msg["message"]["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc_recipients
            ]

        response = session.post(url, json=email_msg)
        if not response.ok:
            _handle_graph_error(response)

        return {
            "status": "sent",
            "subject": subject,
            "recipients": {
                "to": to_recipients,
                "cc": cc_recipients or [],
                "bcc": bcc_recipients or [],
            },
        }

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error sending shared mailbox mail: {str(e)}")


def send_shared_mailbox_draft(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    access_token: str = None,
) -> Dict:
    """
    Sends an existing draft message from a shared Exchange mailbox.

    Uses POST /users/{mailbox_email}/messages/{message_id}/send to dispatch
    a draft that was previously created via create_shared_mailbox_draft.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID of the draft to send
        access_token: Amplify API access token

    Returns:
        Dict confirming the send action

    Raises:
        SharedInboxError: If sending fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/send"

        logger.info("Sending draft %s from shared mailbox %s for user %s", message_id, mailbox_email, current_user)
        response = session.post(url, json={})

        if response.status_code not in (202, 204):
            _handle_graph_error(response)

        return {"status": "sent", "message_id": message_id, "from": mailbox_email}

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error sending draft from shared mailbox: {str(e)}")


def add_shared_mailbox_attachment(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    name: str,
    content_type: str,
    content_bytes: str,
    is_inline: bool = False,
    access_token: str = None,
) -> Dict:
    """
    Adds an attachment to a message in a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID
        name: Attachment file name
        content_type: MIME type of the attachment
        content_bytes: Base64 encoded content of the attachment
        is_inline: Whether the attachment is inline (default: False)
        access_token: Amplify API access token

    Returns:
        Dict containing the added attachment detail

    Raises:
        SharedInboxError: If the attachment operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/attachments"
        payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": content_bytes,
            "isInline": is_inline,
        }
        response = session.post(url, json=payload)
        if not response.ok:
            _handle_graph_error(response)

        attachment = response.json()
        return {
            "id": attachment.get("id"),
            "name": attachment.get("name"),
            "contentType": attachment.get("contentType"),
            "size": attachment.get("size"),
            "isInline": attachment.get("isInline", False),
            "lastModifiedDateTime": attachment.get("lastModifiedDateTime"),
        }

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error adding shared mailbox attachment: {str(e)}")


def delete_shared_mailbox_draft(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    access_token: str = None,
) -> Dict:
    """
    Deletes a draft message from a shared Exchange mailbox.

    Args:
        current_user: Amplify user identifier
        mailbox_email: Email address of the shared mailbox
        message_id: Graph API message ID of the draft to delete
        access_token: Amplify API access token

    Returns:
        Dict containing deletion status

    Raises:
        SharedInboxError: If deletion fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": message_id}

        _handle_graph_error(response)

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error deleting shared mailbox draft: {str(e)}")
