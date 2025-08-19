import json
import requests
from typing import Dict, List, Optional, Union
from datetime import datetime
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_outlook"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class OutlookError(Exception):
    """Base exception for Outlook operations"""

    pass


class MessageNotFoundError(OutlookError):
    """Raised when a message cannot be found"""

    pass


class FolderNotFoundError(OutlookError):
    """Raised when a mail folder cannot be found"""

    pass


class AttachmentError(OutlookError):
    """Raised when attachment operations fail"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "message" in error_message:
            raise MessageNotFoundError("Message not found")
        elif "folder" in error_message:
            raise FolderNotFoundError("Folder not found")
        raise OutlookError("Resource not found")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise OutlookError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_messages(
    current_user: str,
    folder_id: str = "Inbox",
    top: int = 10,
    skip: int = 0,
    filter_query: Optional[str] = None,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists messages in a specified mail folder with pagination and filtering support.

    Args:
        current_user: User identifier
        folder_id: Folder ID or well-known name (default: "Inbox")
        top: Maximum number of messages to retrieve
        skip: Number of messages to skip
        filter_query: OData filter query

    Returns:
        List of message details

    Raises:
        FolderNotFoundError: If folder doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders/{folder_id}/messages"

        # Add filter if provided, but keep query VERY simple to avoid Graph API complexity limits
        if filter_query:
            # When filtering, use minimal parameters to avoid complexity error
            params = {
                "$filter": filter_query,
                "$top": top
                # Skip $skip, $select, $orderby, and $expand to avoid "too complex" error
            }
        else:
            # When not filtering, use full parameter set
            params = {
                "$top": top, 
                "$skip": skip,
                "$select": "id,subject,from,receivedDateTime,hasAttachments,importance,isDraft,isRead,categories",
                "$orderby": "receivedDateTime desc",
                "$expand": "singleValueExtendedProperties($filter=id eq 'String {00020386-0000-0000-C000-000000000046} Name msip_labels')"
            }

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)

        messages = response.json().get("value", [])
        return [format_message(msg, detailed=False, include_body=False) for msg in messages]

    except requests.RequestException as e:
        raise OutlookError(f"Network error while listing messages: {str(e)}")


def get_message_details(
    current_user: str,
    message_id: str,
    include_body: bool = True,
    access_token: str = None,
) -> Dict:
    """
    Gets detailed information about a specific message.

    Args:
        current_user: User identifier
        message_id: Message ID
        include_body: Whether to include message body

    Returns:
        Dict containing message details

    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"

        # Build select fields based on include_body parameter
        if include_body:
            select_fields = "id,subject,body,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,categories"
        else:
            select_fields = "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments,categories"

        params = {
            "$select": select_fields,
            "$expand": "singleValueExtendedProperties($filter=id eq 'String {00020386-0000-0000-C000-000000000046} Name msip_labels')"
        }

        response = session.get(url, params=params)

        if not response.ok:
            handle_graph_error(response)
        
        # Pass include_body to format_message so it knows whether to process body content
        return format_message(response.json(), detailed=True, include_body=include_body)

    except requests.RequestException as e:
        raise OutlookError(f"Network error while fetching message: {str(e)}")


def send_mail(
    current_user: str,
    subject: str,
    body: str,
    to_recipients: List[str],
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    access_token: str = None,
) -> Dict:
    """
    Sends an email with support for CC, BCC, and importance levels.

    Args:
        current_user: User identifier
        subject: Email subject
        body: Email body content
        to_recipients: List of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Message importance ('low', 'normal', 'high')

    Returns:
        Dict containing send status

    Raises:
        OutlookError: If send operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/sendMail"

        # Validate inputs
        if not subject or not body:
            raise OutlookError("Subject and body are required")

        if not to_recipients:
            raise OutlookError("At least one recipient is required")

        if importance not in ["low", "normal", "high"]:
            raise OutlookError("Invalid importance level")

        # Validate email formats
        for email in to_recipients + (cc_recipients or []) + (bcc_recipients or []):
            if not "@" in email:
                raise OutlookError(f"Invalid email address format: {email}")

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
            "recipients": {
                "to": to_recipients,
                "cc": cc_recipients or [],
                "bcc": bcc_recipients or [],
            },
        }

    except requests.RequestException as e:
        raise OutlookError(f"Network error while sending mail: {str(e)}")


def delete_message(current_user: str, message_id: str, access_token: str) -> Dict:
    """
    Deletes a message.

    Args:
        current_user: User identifier
        message_id: Message ID to delete

    Returns:
        Dict containing deletion status

    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": message_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise OutlookError(f"Network error while deleting message: {str(e)}")


def get_attachments(
    current_user: str, message_id: str, access_token: str = None
) -> List[Dict]:
    """
    Gets attachments for a specific message.

    Args:
        current_user: User identifier
        message_id: Message ID

    Returns:
        List of attachment details

    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        attachments = response.json().get("value", [])
        return [format_attachment(attachment) for attachment in attachments]

    except requests.RequestException as e:
        raise OutlookError(f"Network error while getting attachments: {str(e)}")


def download_attachment(
    current_user: str, message_id: str, attachment_id: str, access_token: str = None
) -> Dict:
    """
    Downloads an attachment from a specific message.
    
    For files under 7MB, returns base64-encoded content directly.
    For larger files, returns a temporary download URL to avoid API Gateway limits.

    Args:
        current_user: User identifier
        message_id: Message ID
        attachment_id: Attachment ID
        access_token: Optional OAuth token

    Returns:
        Dict with attachment content/URL and metadata

    Raises:
        MessageNotFoundError: If message doesn't exist
        AttachmentError: If attachment doesn't exist or download fails
        OutlookError: For other failures
    
    Notes:
        - API Gateway has 10MB response limit, base64 adds ~33% overhead
        - Files >7MB return download URLs instead of content
        - Supports fileAttachment types only
        - itemAttachment and referenceAttachment return appropriate guidance
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        
        # Get attachment metadata
        metadata_url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments/{attachment_id}"
        metadata_response = session.get(metadata_url)

        if not metadata_response.ok:
            if metadata_response.status_code == 404:
                raise AttachmentError("Attachment not found")
            handle_graph_error(metadata_response)

        attachment_metadata = metadata_response.json()
        attachment_type = attachment_metadata.get("@odata.type")
        file_size = attachment_metadata.get("size", 0)
        
        # API Gateway limit consideration: 10MB response limit
        # Base64 adds ~33% overhead, so 7MB is safe limit
        SIZE_LIMIT_BYTES = 7 * 1024 * 1024  # 7MB
        
        if attachment_type == "#microsoft.graph.fileAttachment":
            result = {
                "id": attachment_metadata.get("id"),
                "name": attachment_metadata.get("name"),
                "contentType": attachment_metadata.get("contentType"),
                "size": file_size,
                "isInline": attachment_metadata.get("isInline", False),
                "lastModifiedDateTime": attachment_metadata.get("lastModifiedDateTime")
            }
            
            if file_size <= SIZE_LIMIT_BYTES:
                # Small file - return base64 content directly
                content_url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments/{attachment_id}/$value"
                content_response = session.get(content_url)
                
                if content_response.ok:
                    import base64
                    result["contentBytes"] = base64.b64encode(content_response.content).decode('utf-8')
                    result["deliveryMethod"] = "direct_content"
                else:
                    # Fallback to contentBytes from metadata
                    result["contentBytes"] = attachment_metadata.get("contentBytes")
                    result["deliveryMethod"] = "metadata_content"
            else:
                # Large file - return download URL to avoid API Gateway limits
                result["downloadUrl"] = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments/{attachment_id}/$value"
                result["deliveryMethod"] = "download_url"
                result["note"] = f"File too large ({file_size:,} bytes) for direct API response. Use downloadUrl with authentication headers."
                
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
                "error": "Item attachments (embedded Outlook items) require special handling"
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
                "note": "Reference attachment - use sourceUrl to access the cloud-stored file"
            }
        else:
            raise AttachmentError(f"Unsupported attachment type: {attachment_type}")

    except requests.RequestException as e:
        raise OutlookError(f"Network error while downloading attachment: {str(e)}")


def parse_msip_label(extended_properties: List[Dict]) -> Dict:
    """
    Parse Microsoft Information Protection label from extended properties
    
    Args:
        extended_properties: List of singleValueExtendedProperties from Graph API
        
    Returns:
        Dict containing sensitivity level and label information
    """
    sensitivity_info = {
        "level": 1,
        "label": "normal",
        "is_sensitive": False
    }
    
    if not extended_properties:
        return sensitivity_info
    
    # print(f"DEBUG: Extended properties found: {len(extended_properties)}")
    
    # Check for MSIP labels property
    for prop in extended_properties:
        prop_id = prop.get("id", "")
        prop_value = prop.get("value", "")
        
        # print(f"DEBUG: Property ID: {prop_id}, Value: {prop_value}")
        
        # Check for MSIP labels property
        if "msip_labels" in prop_id.lower():
            if prop_value:
                
                # Parse the structured MSIP label data
                # Format: MSIP_Label_<guid>_Name=Level 4 Critical;MSIP_Label_<guid>_...
                
                # Extract the Name field from the MSIP label
                name_match = None
                if "_Name=" in prop_value:
                    # Find the Name field
                    parts = prop_value.split(';')
                    for part in parts:
                        if '_Name=' in part:
                            name_match = part.split('_Name=')[1]
                            break
                
                if name_match:
                    label_lower = name_match.lower()
                    
                    # Level 4 (Critical/Confidential) - check for level 4 or critical keywords
                    if any(keyword in label_lower for keyword in [
                        "level 4", "critical", "confidential", "restricted", "secret", 
                        "highly confidential", "classified", "sensitive", "proprietary"
                    ]):
                        sensitivity_info.update({
                            "level": 4,
                            "label": "confidential",
                            "is_sensitive": True
                        })
                        print(f"DEBUG: Detected level 4 sensitivity from MSIP label name")
                    
                    # Level 3 (Private/Internal) - check for level 3 or internal keywords
                    elif any(keyword in label_lower for keyword in [
                        "level 3", "internal", "private", "company", "organization"
                    ]):
                        sensitivity_info.update({
                            "level": 3,
                            "label": "private",
                            "is_sensitive": False
                        })
                        print(f"DEBUG: Detected level 3 sensitivity from MSIP label name")
                    
                    # Level 2 (Personal) - check for level 2 or personal keywords
                    elif any(keyword in label_lower for keyword in [
                        "level 2", "personal"
                    ]):
                        sensitivity_info.update({
                            "level": 2,
                            "label": "personal",
                            "is_sensitive": False
                        })
                        print(f"DEBUG: Detected level 2 sensitivity from MSIP label name")
                    
                    # Level 1 (Public/Normal) - check for level 1 or public keywords
                    elif any(keyword in label_lower for keyword in [
                        "level 1", "public", "non-sensitive", "general", "unrestricted"
                    ]):
                        sensitivity_info.update({
                            "level": 1,
                            "label": "normal",
                            "is_sensitive": False
                        })
                        print(f"DEBUG: Detected level 1 (public) sensitivity from MSIP label name")
                    
                    else:
                        print(f"DEBUG: MSIP label name found but no sensitivity keywords matched: {name_match}")
                    
                    # Store both the extracted name and the full metadata
                    sensitivity_info["displayName"] = name_match
                    sensitivity_info["fullMetadata"] = prop_value
                    
                else:
                    # print(f"DEBUG: Could not extract Name field from MSIP label metadata")
                    # Fallback to searching the entire metadata string for patterns
                    metadata_lower = prop_value.lower()
                    if any(keyword in metadata_lower for keyword in ["level 4", "critical", "confidential"]):
                        sensitivity_info.update({
                            "level": 4,
                            "label": "confidential", 
                            "is_sensitive": True
                        })
                        print(f"DEBUG: Detected level 4 sensitivity from full metadata fallback")
                    elif any(keyword in metadata_lower for keyword in ["level 3", "internal", "private"]):
                        sensitivity_info.update({
                            "level": 3,
                            "label": "private",
                            "is_sensitive": False
                        })
                        print(f"DEBUG: Detected level 3 sensitivity from full metadata fallback")
                    elif any(keyword in metadata_lower for keyword in ["level 2", "personal"]):
                        sensitivity_info.update({
                            "level": 2,
                            "label": "personal",
                            "is_sensitive": False
                        })
                        print(f"DEBUG: Detected level 2 sensitivity from full metadata fallback")
                    else:
                        # If we have MSIP metadata but can't parse it, check if this is a known sensitive GUID
                        # The GUID 123ebcca-f57c-4bc1-a7cd-943e207777a8 appears to be your Level 4 label
                        if "123ebcca-f57c-4bc1-a7cd-943e207777a8" in prop_value:
                            sensitivity_info.update({
                                "level": 4,
                                "label": "confidential",
                                "is_sensitive": True
                            })
                            print(f"DEBUG: Detected level 4 sensitivity from known GUID pattern")
                        else:
                            print(f"DEBUG: MSIP metadata found but could not determine sensitivity level")
                    
                    # Store the metadata we have
                    sensitivity_info["fullMetadata"] = prop_value
                
                break
    
    # print(f"DEBUG: Final sensitivity info: {sensitivity_info}")
    return sensitivity_info


def format_message(message: Dict, detailed: bool = False, include_body: bool = True) -> Dict:
    """Format message data consistently"""
    
    # Parse Microsoft Information Protection label from extended properties
    extended_properties = message.get("singleValueExtendedProperties", [])
    sensitivity_info = parse_msip_label(extended_properties)
    
    # Fallback: Check categories for sensitivity indicators if no MSIP label found
    if sensitivity_info["level"] == 1:
        categories = message.get("categories", [])
        if categories:
            category_text = " ".join(categories).lower()
            if "confidential" in category_text or "restricted" in category_text:
                sensitivity_info.update({
                    "level": 4,
                    "label": "confidential",
                    "is_sensitive": True
                })
            elif "private" in category_text:
                sensitivity_info.update({
                    "level": 3,
                    "label": "private",
                    "is_sensitive": False
                })
            elif "personal" in category_text:
                sensitivity_info.update({
                    "level": 2,
                    "label": "personal",
                    "is_sensitive": False
                })
    
    is_level_4_sensitive = sensitivity_info["is_sensitive"]
    
    formatted = {
        "id": message["id"],
        "subject": message.get("subject", ""),
        "from": message.get("from", {}).get("emailAddress", {}).get("address", ""),
        "receivedDateTime": message.get("receivedDateTime", ""),
        "hasAttachments": message.get("hasAttachments", False),
        "importance": message.get("importance", "normal"),
        "isDraft": message.get("isDraft", False),
        "isRead": message.get("isRead", False),
        "sensitivity": sensitivity_info["level"],
        "sensitivityLabel": sensitivity_info["label"],
    }

    # Add attention note for level 4 sensitive emails
    if is_level_4_sensitive:
        formatted["attentionNote"] = "This email contains sensitive data and cannot be viewed."

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
        
        # Only include body content if requested and available
        if include_body and "body" in message:
            # For level 4 sensitive emails, redact the body content
            if is_level_4_sensitive:
                detailed_fields["body"] = "Non-viewable sensitive content"
                detailed_fields["bodyType"] = "text"
            else:
                detailed_fields["body"] = message.get("body", {}).get("content", "")
                detailed_fields["bodyType"] = message.get("body", {}).get("contentType", "text")
        elif include_body:
            # Body was requested but not available in response
            detailed_fields["body"] = ""
            detailed_fields["bodyType"] = "text"
        # If include_body is False, don't add body fields at all
        
        formatted.update(detailed_fields)

    return formatted


def format_attachment(attachment: Dict) -> Dict:
    """Format attachment data consistently"""
    return {
        "id": attachment["id"],
        "name": attachment.get("name", ""),
        "contentType": attachment.get("contentType", ""),
        "size": attachment.get("size", 0),
        "isInline": attachment.get("isInline", False),
        "lastModifiedDateTime": attachment.get("lastModifiedDateTime", ""),
    }


def update_message(
    current_user: str, message_id: str, changes: Dict, access_token: str = None
) -> Dict:
    """
    Updates properties of a specific message.

    Args:
        current_user: User identifier
        message_id: The ID of the message to update
        changes: Dictionary of properties to update (e.g., {"isRead": True})
        access_token: Optional access token

    Returns:
        Dict containing update status

    Raises:
        OutlookError: For update failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"
        response = session.patch(url, json=changes)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "updated", "id": message_id, "changes": changes}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while updating message: {str(e)}")


def create_draft(
    current_user: str,
    subject: str,
    body: str,
    to_recipients: Optional[List[str]] = None,
    cc_recipients: Optional[List[str]] = None,
    bcc_recipients: Optional[List[str]] = None,
    importance: str = "normal",
    access_token: str = None,
) -> Dict:
    """
    Creates a draft message.

    Args:
        current_user: User identifier
        subject: Draft email subject
        body: Draft email body content
        to_recipients: Optional list of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Importance level ('low', 'normal', 'high')
        access_token: Optional access token

    Returns:
        Dict containing the draft message details

    Raises:
        OutlookError: If creation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages"
        payload = {
            "subject": subject,
            "body": {"contentType": "text", "content": body},
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
        }
    except requests.RequestException as e:
        raise OutlookError(f"Network error while creating draft: {str(e)}")


def send_draft(current_user: str, message_id: str, access_token: str = None) -> Dict:
    """
    Sends a draft message.

    Args:
        current_user: User identifier
        message_id: The ID of the draft message to send
        access_token: Optional access token

    Returns:
        Dict confirming the send action

    Raises:
        OutlookError: If sending fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/send"
        response = session.post(url, json={})
        if response.status_code not in [202, 204]:
            handle_graph_error(response)
        return {"status": "sent", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while sending draft: {str(e)}")


def reply_to_message(
    current_user: str, message_id: str, comment: str, access_token: str = None
) -> Dict:
    """
    Sends a reply to a specific message.

    Args:
        current_user: User identifier
        message_id: The ID of the message to reply to
        comment: The reply comment content
        access_token: Optional access token

    Returns:
        Dict confirming the reply action

    Raises:
        OutlookError: If reply fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/reply"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while replying: {str(e)}")


def reply_all_message(
    current_user: str, message_id: str, comment: str, access_token: str = None
) -> Dict:
    """
    Sends a reply-all to a specific message.

    Args:
        current_user: User identifier
        message_id: The ID of the message to reply to
        comment: The reply comment content
        access_token: Optional access token

    Returns:
        Dict confirming the reply-all action

    Raises:
        OutlookError: If reply-all fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/replyAll"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied_all", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while replying all: {str(e)}")


def forward_message(
    current_user: str,
    message_id: str,
    comment: str,
    to_recipients: List[str],
    access_token: str = None,
) -> Dict:
    """
    Forwards a specific message.

    Args:
        current_user: User identifier
        message_id: The ID of the message to forward
        comment: The comment to include with the forwarded message
        to_recipients: List of recipient email addresses to forward the message to
        access_token: Optional access token

    Returns:
        Dict confirming the forward action

    Raises:
        OutlookError: If forward fails or recipients are missing
    """
    if not to_recipients:
        raise OutlookError("At least one recipient is required to forward a message")
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/forward"
        payload = {
            "comment": comment,
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to_recipients
            ],
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "forwarded", "id": message_id, "recipients": to_recipients}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while forwarding message: {str(e)}")


def move_message(
    current_user: str,
    message_id: str,
    destination_folder_id: str,
    access_token: str = None,
) -> Dict:
    """
    Moves a specific message to a different folder.

    Args:
        current_user: User identifier
        message_id: The ID of the message to move
        destination_folder_id: The target folder ID
        access_token: Optional access token

    Returns:
        Dict containing the moved message details

    Raises:
        OutlookError: If the move operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/move"
        payload = {"destinationId": destination_folder_id}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_message(response.json(), detailed=True, include_body=False)
    except requests.RequestException as e:
        raise OutlookError(f"Network error while moving message: {str(e)}")


def list_folders(current_user: str, access_token: str = None) -> List[Dict]:
    """
    Lists all mail folders.

    Args:
        current_user: User identifier
        access_token: Optional access token

    Returns:
        List of mail folder details

    Raises:
        OutlookError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json().get("value", [])
    except requests.RequestException as e:
        raise OutlookError(f"Network error while listing folders: {str(e)}")


def get_folder_details(
    current_user: str, folder_id: str, access_token: str = None
) -> Dict:
    """
    Retrieves details of a specific mail folder.

    Args:
        current_user: User identifier
        folder_id: The ID of the mail folder
        access_token: Optional access token

    Returns:
        Dict containing folder details

    Raises:
        OutlookError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders/{folder_id}"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise OutlookError(f"Network error while retrieving folder details: {str(e)}")


def add_attachment(
    current_user: str,
    message_id: str,
    name: str,
    content_type: str,
    content_bytes: str,
    is_inline: bool = False,
    access_token: str = None,
) -> Dict:
    """
    Adds an attachment to a specific message.

    Args:
        current_user: User identifier
        message_id: The ID of the message
        name: Attachment file name
        content_type: MIME type of the attachment
        content_bytes: Base64 encoded content of the attachment
        is_inline: Whether the attachment is inline (default: False)
        access_token: Optional access token

    Returns:
        Dict containing the added attachment details

    Raises:
        OutlookError: If the attachment operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments"
        payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": content_bytes,
            "isInline": is_inline,
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_attachment(response.json())
    except requests.RequestException as e:
        raise OutlookError(f"Network error while adding attachment: {str(e)}")


def delete_attachment(
    current_user: str, message_id: str, attachment_id: str, access_token: str = None
) -> Dict:
    """
    Deletes a specific attachment from a message.

    Args:
        current_user: User identifier
        message_id: The ID of the message
        attachment_id: The ID of the attachment to delete
        access_token: Optional access token

    Returns:
        Dict confirming deletion

    Raises:
        OutlookError: If deletion fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments/{attachment_id}"
        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "attachment deleted", "attachment_id": attachment_id}
        handle_graph_error(response)
    except requests.RequestException as e:
        raise OutlookError(f"Network error while deleting attachment: {str(e)}")


def search_messages(
    current_user: str,
    search_query: str,
    top: int = 10,
    access_token: str = None,
) -> List[Dict]:
    """
    Searches messages for a given query string using the Microsoft Graph API's $search parameter.
    
    Note: Microsoft Graph API does not support pagination (skip) with search queries.

    Args:
        current_user: User identifier
        search_query: A string search query (e.g., "meeting")
        top: Maximum number of messages to return (1-100)
        access_token: Optional access token

    Returns:
        List of message details matching the search query

    Raises:
        OutlookError: If the search operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages"
        params = {
            "$top": top, 
            "$search": f'"{search_query}"',
            "$select": "id,subject,from,receivedDateTime,hasAttachments,importance,isDraft,isRead,categories",
            "$expand": "singleValueExtendedProperties($filter=id eq 'String {00020386-0000-0000-C000-000000000046} Name msip_labels')"
        }
        # The Graph API requires the ConsistencyLevel header set to eventual when using $search
        session.headers.update({"ConsistencyLevel": "eventual"})
        response = session.get(url, params=params)
        if not response.ok:
            handle_graph_error(response)
        messages = response.json().get("value", [])
        return [format_message(msg, detailed=False, include_body=False) for msg in messages]
    except requests.RequestException as e:
        raise OutlookError(f"Network error while searching messages: {str(e)}")
