import traceback

from common.ops import vop
from events.email_sender_controls import add_allowed_sender, remove_allowed_sender, list_allowed_senders
from events.event_templates import remove_event_template, get_event_template, list_event_templates_for_user, \
    add_event_template, is_event_template_tag_available
from events.mock import generate_ses_event
from service.agent_queue import route_queue_event


@vop(
    path="/vu-agent/add-event-template",
    tags=["events","default"],
    name="addEventTemplate",
    description="Add or update an event template.",
    params={
        "tag": "The event tag.",
        "prompt": "The structured prompt.",
        "assistantId": "The assistant alias (optional)."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"},
            "prompt": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["role", "content"]
                }
            },
            "assistantId": {"type": "string"}
        },
        "required": ["tag", "prompt"]
    }
)
def handle_add_event_template(current_user, access_token, tag, prompt, assistant_id=None, account=None, description=None):
    try:

        account = account or "agent-account"
        description = description or "agent-event-template-key"
        return add_event_template(current_user, access_token, tag, prompt, account, description, assistant_id)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to add event template. Please try again later."
        }


@vop(
    path="/vu-agent/remove-event-template",
    tags=["events","default"],
    name="removeEventTemplate",
    description="Remove an event template.",
    params={
        "tag": "The event tag."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"}
        },
        "required": ["tag"]
    }
)
def handle_remove_event_template(current_user, access_token, tag):
    try:
        return remove_event_template(current_user, tag)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to remove event template. Please try again later."
        }


@vop(
    path="/vu-agent/get-event-template",
    tags=["events","default"],
    name="getEventTemplate",
    description="Retrieve an event template.",
    params={
        "tag": "The event tag."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"}
        },
        "required": ["tag"]
    }
)
def handle_get_event_template(current_user, access_token, tag):
    try:
        return get_event_template(current_user, tag)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to retrieve event template. Please try again later."
        }


@vop(
    path="/vu-agent/list-event-templates",
    tags=["events","default"],
    name="listEventTemplatesForUser",
    description="List all event templates for the current user.",
    params={},
    schema={
        "type": "object",
        "properties": {}
    }
)
def handle_list_event_templates_for_user(current_user, access_token):
    try:
        return list_event_templates_for_user(current_user)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to list event templates. Please try again later."
        }


@vop(
    path="/vu-agent/add-allowed-sender",
    tags=["email","default"],
    name="addAllowedSender",
    description="Add an allowed sender for a tag.",
    params={
        "tag": "The event tag.",
        "sender": "The sender email or regex pattern."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"},
            "sender": {"type": "string"}
        },
        "required": ["tag", "sender"]
    }
)
def handle_add_allowed_sender(current_user, access_token, tag, sender):
    try:
        return add_allowed_sender(current_user, tag, sender)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to add allowed sender. Please try again later."
        }


@vop(
    path="/vu-agent/remove-allowed-sender",
    tags=["email","default"],
    name="removeAllowedSender",
    description="Remove an allowed sender for a tag.",
    params={
        "tag": "The event tag.",
        "sender": "The sender email or regex pattern."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"},
            "sender": {"type": "string"}
        },
        "required": ["tag", "sender"]
    }
)
def handle_remove_allowed_sender(current_user, access_token, tag, sender):
    try:
        return remove_allowed_sender(current_user, tag, sender)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to remove allowed sender. Please try again later."
        }


@vop(
    path="/vu-agent/test-send-email-notification",
    tags=["email", "default"],
    name="testSendEmailNotification",
    description="Generate an email notification event.",
    params={
        "sender": "The sender email. This must always be set to the current user.",
        "receiver": "The receiver email.",
        "subject": "The subject of the email.",
        "body": "The body of the email."
    },
    schema={
        "type": "object",
        "properties": {
            "sender": {
                "type": "string",
                "description": "The sender of the email. This must be set to the current user."
            },
            "receiver": {"type": "string", "description": "Receiver's email address"},
            "subject": {"type": "string", "description": "Subject of the email."},
            "body": {"type": "string", "description": "Body of the email."}
        },
        "required": ["sender", "receiver", "subject", "body"]
    }
)
def test_send_email_notification(current_user, access_token, sender, receiver, subject, body):
    # Validate that the sender matches the current_user
    if sender != current_user:
        return {
            "success": False,
            "data": None,
            "message": "Sender mismatch: You are not authorized to send as this user."
        }

    try:
        # Generate the email notification event
        email_notification = generate_ses_event(
            sender=sender,
            receiver=receiver,
            subject=subject,
            body=body
        )

        route_queue_event(email_notification, {})

        # Here, you might do something with the generated email notification,
        # such as saving it to a database or sending it to a queue

        return {
            "success": True,
            "data": email_notification,
            "message": "Email notification event generated successfully."
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": f"Server error: Unable to generate email notification. Error: {str(e)}"
        }


@vop(
    path="/vu-agent/list-allowed-senders",
    tags=["email","default"],
    name="listAllowedSenders",
    description="List all allowed senders for a tag.",
    params={
        "tag": "The event tag."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"}
        },
        "required": ["tag"]
    }
)
def handle_list_allowed_senders(current_user, access_token, tag):
    try:
        return list_allowed_senders(current_user, tag)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": [],
            "message": "Server error: Unable to list allowed senders. Please try again later."
        }


@vop(
    path="/vu-agent/is-event-template-tag-available",
    tags=["events", "default"],
    name="isEventTemplateTagAvailable",
    description="Check if an event template tag is available for the current user and assistant.",
    params={
        "tag": "The event tag to check.",
        "assistantId": "The assistant ID (optional)."
    },
    schema={
        "type": "object",
        "properties": {
            "tag": {"type": "string"},
            "assistantId": {"type": "string"}
        },
        "required": ["tag"]
    }
)
def handle_is_event_template_tag_available(current_user, access_token, tag, assistant_id=None):
    try:
        return is_event_template_tag_available(current_user, tag, assistant_id)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": False,
            "message": "Server error: Unable to check tag availability. Please try again later."
        }