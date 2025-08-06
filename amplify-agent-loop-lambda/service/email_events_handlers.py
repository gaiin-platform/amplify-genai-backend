import traceback

from pycommon.api.ops import api_tool
from events.email_sender_controls import (
    add_allowed_sender,
    remove_allowed_sender,
    list_allowed_senders,
)
from events.event_templates import (
    remove_event_template,
    get_event_template,
    list_event_templates_for_user,
    add_event_template,
    is_event_template_tag_available,
    list_event_templates_tags_for_user,
)
from events.mock import generate_ses_event
from service.agent_queue import route_queue_event


@api_tool(
    path="/vu-agent/add-event-template",
    tags=["events", "default"],
    name="addEventTemplate",
    description="Add or update an event template.",
    parameters={
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "The event tag."},
            "prompt": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["role", "content"],
                },
                "description": "The structured prompt.",
            },
            "assistantId": {
                "type": "string",
                "description": "The assistant alias (optional).",
            },
        },
        "required": ["tag", "prompt"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_add_event_template(
    current_user,
    access_token,
    tag,
    prompt,
    assistant_id=None,
    account=None,
    description=None,
):
    try:

        account = account or "agent-account"
        description = description or "agent-event-template-key"
        return add_event_template(
            current_user, access_token, tag, prompt, account, description, assistant_id
        )
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to add event template. Please try again later.",
        }


@api_tool(
    path="/vu-agent/remove-event-template",
    tags=["events", "default"],
    name="removeEventTemplate",
    description="Remove an event template.",
    parameters={
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "The event tag."}},
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_remove_event_template(current_user, access_token, tag):
    try:
        return remove_event_template(current_user, tag, access_token)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to remove event template. Please try again later.",
        }


@api_tool(
    path="/vu-agent/get-event-template",
    tags=["events", "default"],
    name="getEventTemplate",
    description="Retrieve an event template.",
    parameters={
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "The event tag."}},
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "The user who owns the event template",
                    },
                    "tag": {"type": "string", "description": "The event tag"},
                    "assistantId": {
                        "type": "string",
                        "description": "The assistant ID (if present)",
                    },
                    "prompt": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The content of the prompt message",
                                },
                                "role": {
                                    "type": "string",
                                    "description": "The role (e.g., 'system', 'user', 'assistant')",
                                },
                            },
                            "required": ["content", "role"],
                        },
                        "description": "The structured prompt as an array of message objects",
                    },
                    "assistant": {
                        "type": "object",
                        "description": "Resolved assistant data (if assistantId is present)",
                    },
                },
                "description": "The event template data if successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_get_event_template(current_user, access_token, tag):
    try:
        response = get_event_template(current_user, tag)
        if not response["success"]:
            response["data"] = None
        else:
            for attr in ["apiKeyId", "s_apiKey"]:
                if attr in response["data"]:
                    del response["data"][attr]

        return response
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to retrieve event template. Please try again later.",
        }


@api_tool(
    path="/vu-agent/list-event-templates",
    tags=["events", "default"],
    name="listEventTemplatesForUser",
    description="List all event templates for the current user.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "The user who owns the event template",
                    },
                    "tag": {"type": "string", "description": "The event tag"},
                    "assistantId": {
                        "type": "string",
                        "description": "The assistant ID (if present)",
                    },
                    "prompt": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The content of the prompt message",
                                },
                                "role": {
                                    "type": "string",
                                    "description": "The role (e.g., 'system', 'user', 'assistant')",
                                },
                            },
                            "required": ["content", "role"],
                        },
                        "description": "The structured prompt as an array of message objects",
                    },
                    "assistant": {
                        "type": "object",
                        "description": "Resolved assistant data (if assistantId is present)",
                    },
                },
                "description": "The event template data if successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_list_event_templates_for_user(current_user, access_token):
    try:
        return list_event_templates_for_user(current_user)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to list event templates. Please try again later.",
        }


@api_tool(
    path="/vu-agent/list-event-template-tags",
    tags=["events", "default"],
    name="listEventTemplateTags",
    description="List all event template tags for the current user.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of event template tags if successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_list_event_template_tags(current_user, access_token):
    try:
        return list_event_templates_tags_for_user(current_user)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to list event template tags. Please try again later.",
        }


@api_tool(
    path="/vu-agent/add-allowed-sender",
    tags=["email", "default"],
    name="addAllowedSender",
    description="Add an allowed sender for a tag.",
    parameters={
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "The event tag."},
            "sender": {
                "type": "string",
                "description": "The sender email or regex pattern.",
            },
        },
        "required": ["tag", "sender"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_add_allowed_sender(current_user, access_token, tag, sender):
    try:
        return add_allowed_sender(current_user, tag, sender)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to add allowed sender. Please try again later.",
        }


@api_tool(
    path="/vu-agent/remove-allowed-sender",
    tags=["email", "default"],
    name="removeAllowedSender",
    description="Remove an allowed sender for a tag.",
    parameters={
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "The event tag."},
            "sender": {
                "type": "string",
                "description": "The sender email or regex pattern.",
            },
        },
        "required": ["tag", "sender"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_remove_allowed_sender(current_user, access_token, tag, sender):
    try:
        return remove_allowed_sender(current_user, tag, sender)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "message": "Server error: Unable to remove allowed sender. Please try again later.",
        }


@api_tool(
    path="/vu-agent/test-send-email-notification",
    tags=["email", "default"],
    name="testSendEmailNotification",
    description="Generate an email notification event.",
    parameters={
        "type": "object",
        "properties": {
            "sender": {
                "type": "string",
                "description": "The sender of the email. This must be set to the current user.",
            },
            "receiver": {"type": "string", "description": "Receiver's email address"},
            "subject": {"type": "string", "description": "Subject of the email."},
            "body": {"type": "string", "description": "Body of the email."},
        },
        "required": ["sender", "receiver", "subject", "body"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "description": "The generated email notification event if successful"
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def test_send_email_notification(
    current_user, access_token, sender, receiver, subject, body
):
    # Validate that the sender matches the current_user
    if sender != current_user:
        return {
            "success": False,
            "data": None,
            "message": "Sender mismatch: You are not authorized to send as this user.",
        }

    try:
        # Generate the email notification event
        email_notification = generate_ses_event(
            sender=sender, receiver=receiver, subject=subject, body=body
        )

        route_queue_event(email_notification, {})

        # Here, you might do something with the generated email notification,
        # such as saving it to a database or sending it to a queue

        return {
            "success": True,
            "data": email_notification,
            "message": "Email notification event generated successfully.",
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": f"Server error: Unable to generate email notification. Error: {str(e)}",
        }


@api_tool(
    path="/vu-agent/list-allowed-senders",
    tags=["email", "default"],
    name="listAllowedSenders",
    description="List all allowed senders for a tag.",
    parameters={
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "The event tag."}},
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "array",
                "description": "Array of allowed senders if successful",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_list_allowed_senders(current_user, access_token, tag):
    try:
        return list_allowed_senders(current_user, tag)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": [],
            "message": "Server error: Unable to list allowed senders. Please try again later.",
        }


@api_tool(
    path="/vu-agent/is-event-template-tag-available",
    tags=["events", "default"],
    name="isEventTemplateTagAvailable",
    description="Check if an event template tag is available for the current user and assistant.",
    parameters={
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "The event tag to check."},
            "assistantId": {
                "type": "string",
                "description": "The assistant ID (optional).",
            },
        },
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "available": {
                        "type": "boolean",
                        "description": "Whether the tag is available",
                    }
                },
                "description": "Object containing availability information",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success"],
    },
)
def handle_is_event_template_tag_available(
    current_user, access_token, tag, assistant_id=None
):
    try:
        return is_event_template_tag_available(current_user, tag, assistant_id)
    except Exception:
        traceback.print_exc()
        return {
            "success": False,
            "data": None,
            "message": "Server error: Unable to check tag availability. Please try again later.",
        }
