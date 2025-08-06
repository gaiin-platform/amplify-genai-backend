from googleapiclient.discovery import build
import json
from datetime import datetime, timedelta
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

integration_name = "google_gmail"


def get_gmail_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("gmail", "v1", credentials=credentials)


def compose_and_send_email(
    current_user,
    to,
    subject,
    body,
    cc=None,
    bcc=None,
    schedule_time=None,
    access_token=None,
):
    service = get_gmail_service(current_user, access_token)
    message = MIMEMultipart()
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc
    message.attach(MIMEText(body, "plain"))
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    body = {"raw": raw_message}
    if schedule_time:
        body["scheduleSend"] = {"sendAt": schedule_time.isoformat() + "Z"}

    message = service.users().messages().send(userId="me", body=body).execute()
    return json.dumps({"message_id": message["id"]})


def compose_email_draft(
    current_user, to, subject, body, cc=None, bcc=None, access_token=None
):
    service = get_gmail_service(current_user, access_token)
    message = MIMEMultipart()
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc
    message.attach(MIMEText(body, "plain"))
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw_message}})
        .execute()
    )
    return json.dumps({"draft_id": draft["id"]})


def get_messages_from_date(
    current_user, n, start_date, label=None, fields=None, access_token=None
):
    service = get_gmail_service(current_user, access_token)

    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    elif isinstance(start_date, datetime):
        start_date = start_date.date()

    query = f'after:{start_date.strftime("%Y/%m/%d")}'
    if label:
        query += f" label:{label}"

    messages_result = (
        service.users().messages().list(userId="me", q=query, maxResults=n).execute()
    )
    message_ids = [msg["id"] for msg in messages_result.get("messages", [])]

    detailed_messages = get_messages_details(service, message_ids, fields)

    return json.dumps(detailed_messages)


def get_recent_messages(current_user, n=25, label=None, fields=None, access_token=None):
    service = get_gmail_service(current_user, access_token)
    query = ""
    if label:
        query += f"label:{label}"

    messages_result = (
        service.users().messages().list(userId="me", q=query, maxResults=n).execute()
    )
    message_ids = [msg["id"] for msg in messages_result.get("messages", [])]

    detailed_messages = get_messages_details(service, message_ids, fields)

    return json.dumps(detailed_messages)


def search_messages(current_user, query, fields=None, access_token=None):
    service = get_gmail_service(current_user, access_token)
    messages_result = service.users().messages().list(userId="me", q=query).execute()
    message_ids = [msg["id"] for msg in messages_result.get("messages", [])]

    detailed_messages = get_messages_details(service, message_ids, fields)

    return json.dumps(detailed_messages)


def get_attachment_links(current_user, message_id, access_token=None):
    service = get_gmail_service(current_user, access_token)
    message = service.users().messages().get(userId="me", id=message_id).execute()
    attachments = []
    if "payload" in message and "parts" in message["payload"]:
        for part in message["payload"]["parts"]:
            if "filename" in part and part["filename"]:
                attachments.append(
                    {
                        "filename": part["filename"],
                        "attachment_id": part["body"]["attachmentId"],
                    }
                )
    return json.dumps(attachments)


def get_attachment_content(current_user, message_id, attachment_id, access_token=None):
    service = get_gmail_service(current_user, access_token)
    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    file_data = base64.urlsafe_b64decode(attachment["data"].encode("UTF-8"))
    return file_data


def create_filter(current_user, criteria, action, access_token=None):
    service = get_gmail_service(current_user, access_token)
    filter_object = {"criteria": criteria, "action": action}
    result = (
        service.users()
        .settings()
        .filters()
        .create(userId="me", body=filter_object)
        .execute()
    )
    return json.dumps({"filter_id": result["id"]})


def create_label(current_user, name, access_token=None):
    service = get_gmail_service(current_user, access_token)
    label = {"name": name}
    result = service.users().labels().create(userId="me", body=label).execute()
    return json.dumps({"label_id": result["id"]})


def create_auto_filter_label_rule(
    current_user, criteria, label_name, access_token=None
):
    service = get_gmail_service(current_user, access_token)

    # First, create or get the label
    try:
        label = (
            service.users()
            .labels()
            .create(userId="me", body={"name": label_name})
            .execute()
        )
    except:
        labels = service.users().labels().list(userId="me").execute()
        label = next((l for l in labels["labels"] if l["name"] == label_name), None)
        if not label:
            return json.dumps({"error": "Failed to create or find label"})

    # Then create the filter
    filter_object = {"criteria": criteria, "action": {"addLabelIds": [label["id"]]}}
    result = (
        service.users()
        .settings()
        .filters()
        .create(userId="me", body=filter_object)
        .execute()
    )
    return json.dumps({"filter_id": result["id"], "label_id": label["id"]})


def get_message_details(current_user, message_id, fields=None, access_token=None):
    service = get_gmail_service(current_user, access_token)
    detailed_messages = get_messages_details(service, [message_id], fields)

    return json.dumps(detailed_messages)


def get_messages_details(service, message_ids, fields=None):
    default_fields = ["id", "sender", "subject", "labels", "date"]
    if fields is None:
        fields = default_fields
    elif isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]

    results = []

    for message_id in message_ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        headers = msg["payload"]["headers"]

        result = {}

        for field in fields:
            if field == "id":
                result["id"] = msg["id"]
            elif field == "threadId":
                result["threadId"] = msg["threadId"]
            elif field == "historyId":
                result["historyId"] = msg["historyId"]
            elif field == "sizeEstimate":
                result["sizeEstimate"] = msg["sizeEstimate"]
            elif field == "raw":
                # far too large to return msg['raw']
                result["raw"] = ""
            elif field == "payload":
                # far too large to return
                # result['payload'] = msg['payload']
                result["payload"] = msg["payload"].get("body", {}).get("data", "")
            elif field == "mimeType":
                result["mimeType"] = msg["payload"]["mimeType"]
            elif field == "attachments":
                result["attachments"] = [
                    part
                    for part in msg["payload"].get("parts", [])
                    if "filename" in part and part["filename"]
                ]
            elif field == "sender":
                result["sender"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "from"
                    ),
                    "Unknown",
                )
            elif field == "subject":
                result["subject"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "subject"
                    ),
                    "No Subject",
                )
            elif field == "labels":
                result["labels"] = msg.get("labelIds", [])
            elif field == "date":
                result["date"] = msg["internalDate"]
            elif field == "snippet":
                result["snippet"] = msg.get("snippet", "")
            elif field == "body":
                result["body"] = msg["payload"]["body"].get("data", "")
            elif field == "cc":
                result["cc"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "cc"
                    ),
                    "",
                )
            elif field == "bcc":
                result["bcc"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "bcc"
                    ),
                    "",
                )
            elif field == "deliveredTo":
                result["deliveredTo"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "delivered-to"
                    ),
                    "",
                )
            elif field == "receivedTime":
                result["receivedTime"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "received"
                    ),
                    "",
                )
            elif field == "sentTime":
                result["sentTime"] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == "date"
                    ),
                    "",
                )
            elif field in headers:
                result[field] = next(
                    (
                        header["value"]
                        for header in headers
                        if header["name"].lower() == field.lower()
                    ),
                    "",
                )

        results.append(result)

    return results


def send_draft_email(current_user, draft_id, access_token=None):
    """
    Sends an existing draft email by its ID.

    Args:
        current_user: The user initiating the request
        draft_id: The ID of the draft to send
        access_token: Optional access token

    Returns:
        JSON string containing the message_id of the sent email
    """
    service = get_gmail_service(current_user, access_token)
    message = (
        service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    )
    return json.dumps({"message_id": message["id"]})
