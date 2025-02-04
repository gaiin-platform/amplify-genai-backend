import traceback

from common.ops import vop
from events.email_sender_controls import add_allowed_sender, remove_allowed_sender
from events.event_templates import remove_event_template, get_event_template, list_event_templates_for_user, \
    add_event_template


@vop(
    path="/vu-agent/add-event-template",
    tags=["events"],
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
    tags=["events"],
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
    tags=["events"],
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
    tags=["events"],
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
    tags=["email"],
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
    tags=["email"],
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