"""
Schema for POST /groups/layered_assistants

Handles three actions for group-scoped Layered Assistants:
  - create_or_update: requires layeredAssistant sub-object
  - list:             requires only group_id + action
  - delete:           requires group_id + action + publicId
"""

_layered_assistant_schema = {
    "type": "object",
    "properties": {
        "publicId":    {"type": "string", "description": "astgr/<uuid> — omit or empty string to create new"},
        "name":        {"type": "string", "description": "Display name of the layered assistant"},
        "description": {"type": "string", "description": "Optional description"},
        "rootNode":    {"type": "object", "description": "Root RouterNode of the layered assistant tree"},
    },
    "required": ["name", "rootNode"],
}

manage_group_layered_assistants_schema = {
    "type": "object",
    "properties": {
        "group_id": {
            "type": "string",
            "description": "The ID of the group that owns the layered assistant.",
        },
        "action": {
            "type": "string",
            "enum": ["create_or_update", "list", "delete"],
            "description": "The CRUD action to perform.",
        },
        "layeredAssistant": {
            **_layered_assistant_schema,
            "description": "Required for action=create_or_update.",
        },
        "publicId": {
            "type": "string",
            "description": "Required for action=delete. The astgr/<uuid> public ID to delete.",
        },
    },
    "required": ["group_id", "action"],
}
