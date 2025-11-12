import json
import requests
from typing import Dict, List, Optional

from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_exchange"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"

from pycommon.logger import getLogger
logger = getLogger(integration_name)

class SharedInboxError(Exception):
    pass


class MessageNotFoundError(SharedInboxError):
    pass


class FolderNotFoundError(SharedInboxError):
    pass


def handle_graph_error(response: requests.Response) -> None:
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "message" in error_message:
            raise MessageNotFoundError("Message not found")
        elif "folder" in error_message:
            raise FolderNotFoundError("Folder not found")
        raise SharedInboxError("Resource not found")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise SharedInboxError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_shared_mailbox_messages(
    current_user: str,
    mailbox_email: str,
    folder_id: str = "Inbox",
    top: int = 10,
    skip: int = 0,
    filter_query: Optional[str] = None,
    access_token: str = None,
) -> List[Dict]:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders/{folder_id}/messages"

        if filter_query:
            params = {"$filter": filter_query, "$top": top}
        else:
            params = {
                "$top": top,
                "$skip": skip,
                "$select": "id,subject,from,receivedDateTime,hasAttachments,importance,isDraft,isRead,categories",
                "$orderby": "receivedDateTime desc",
            }

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        messages = response.json().get("value", [])
        return [format_message(msg) for msg in messages]

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while listing messages: {str(e)}")


def get_shared_mailbox_message_details(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    include_body: bool = True,
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}"

        if include_body:
            select_fields = "id,subject,body,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,categories"
        else:
            select_fields = "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,categories"

        params = {"$select": select_fields}

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        return format_message(response.json(), detailed=True, include_body=include_body)

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while fetching message: {str(e)}")


def send_shared_mailbox_mail(
    current_user: str,
    mailbox_email: str,
    subject: str,
    body: str,
    to_recipients: List[str],
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/sendMail"

        if not subject or not body:
            raise SharedInboxError("Subject and body are required")

        if not to_recipients:
            raise SharedInboxError("At least one recipient is required")

        if importance not in ["low", "normal", "high"]:
            raise SharedInboxError("Invalid importance level")

        for email in to_recipients + (cc_recipients or []) + (bcc_recipients or []):
            if "@" not in email:
                raise SharedInboxError(f"Invalid email address format: {email}")

        email_msg = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body},
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
            handle_graph_error(response)

        return {
            "status": "sent",
            "subject": subject,
            "from_mailbox": mailbox_email,
            "recipients": {
                "to": to_recipients,
                "cc": cc_recipients or [],
                "bcc": bcc_recipients or [],
            },
        }

    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while sending mail: {str(e)}")


def reply_to_shared_mailbox_message(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    comment: str,
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/reply"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied", "id": message_id, "mailbox": mailbox_email}
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while replying: {str(e)}")


def reply_all_shared_mailbox_message(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    comment: str,
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/replyAll"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied_all", "id": message_id, "mailbox": mailbox_email}
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while replying all: {str(e)}")


def forward_shared_mailbox_message(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    comment: str,
    to_recipients: List[str],
    access_token: str = None,
) -> Dict:
    if not to_recipients:
        raise SharedInboxError("At least one recipient is required to forward a message")
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/forward"
        payload = {
            "comment": comment,
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to_recipients
            ],
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {
            "status": "forwarded",
            "id": message_id,
            "mailbox": mailbox_email,
            "recipients": to_recipients,
        }
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while forwarding message: {str(e)}")


def list_shared_mailbox_folders(
    current_user: str, mailbox_email: str, access_token: str = None
) -> List[Dict]:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/mailFolders"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json().get("value", [])
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while listing folders: {str(e)}")


def search_shared_mailbox_messages(
    current_user: str,
    mailbox_email: str,
    search_query: str,
    top: int = 10,
    access_token: str = None,
) -> List[Dict]:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages"
        params = {
            "$top": top,
            "$search": f'"{search_query}"',
            "$select": "id,subject,from,receivedDateTime,hasAttachments,importance,isDraft,isRead,categories",
        }
        session.headers.update({"ConsistencyLevel": "eventual"})
        response = session.get(url, params=params)
        if not response.ok:
            handle_graph_error(response)
        messages = response.json().get("value", [])
        return [format_message(msg) for msg in messages]
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while searching messages: {str(e)}")


def create_shared_mailbox_draft(
    current_user: str,
    mailbox_email: str,
    subject: str,
    body: str,
    to_recipients: Optional[List[str]] = None,
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages"
        payload = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "importance": importance,
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
            handle_graph_error(response)

        response_data = response.json()
        return {
            "message_id": response_data.get("id"),
            "mailbox": mailbox_email,
            "status": "draft_created",
        }
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while creating draft: {str(e)}")


def send_shared_mailbox_draft(
    current_user: str,
    mailbox_email: str,
    message_id: str,
    access_token: str = None,
) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{mailbox_email}/messages/{message_id}/send"
        response = session.post(url, json={})
        if response.status_code not in [202, 204]:
            handle_graph_error(response)
        return {"status": "sent", "id": message_id, "mailbox": mailbox_email}
    except requests.RequestException as e:
        raise SharedInboxError(f"Network error while sending draft: {str(e)}")


def format_message(message: Dict, detailed: bool = False, include_body: bool = True) -> Dict:
    formatted = {
        "id": message["id"],
        "subject": message.get("subject", ""),
        "from": message.get("from", {}).get("emailAddress", {}).get("address", ""),
        "receivedDateTime": message.get("receivedDateTime", ""),
        "hasAttachments": message.get("hasAttachments", False),
        "importance": message.get("importance", "normal"),
        "isDraft": message.get("isDraft", False),
        "isRead": message.get("isRead", False),
    }

    if detailed:
        detailed_fields = {
            "toRecipients": [
                r.get("emailAddress", {}).get("address", "")
                for r in message.get("toRecipients", [])
            ],
            "ccRecipients": [
                r.get("emailAddress", {}).get("address", "")
                for r in message.get("ccRecipients", [])
            ],
            "bccRecipients": [
                r.get("emailAddress", {}).get("address", "")
                for r in message.get("bccRecipients", [])
            ],
            "categories": message.get("categories", []),
            "webLink": message.get("webLink", ""),
        }

        if include_body and "body" in message:
            detailed_fields["body"] = message.get("body", {}).get("content", "")
            detailed_fields["bodyType"] = message.get("body", {}).get("contentType", "text")
        elif include_body:
            detailed_fields["body"] = ""
            detailed_fields["bodyType"] = "text"

        formatted.update(detailed_fields)

    return formatted
