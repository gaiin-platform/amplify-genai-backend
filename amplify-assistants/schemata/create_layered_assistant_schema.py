# Schema for create_or_update_layered_assistant
# publicId is optional: omit / empty string → create new, provide → update

# Recursive node type is validated loosely as "object" here;
# deep structural validation happens in the service layer.
_node_schema = {
    "type": "object",
    "properties": {
        "type":        {"type": "string", "enum": ["router", "leaf"]},
        "id":          {"type": "string"},
        "name":        {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["type", "id", "name"],
}

create_layered_assistant_schema = {
    "type": "object",
    "properties": {
        "publicId": {
            "type": "string",
            "description": "Existing layered assistant public ID to update (astr/<uuid> or astgr/<uuid>). Omit to create.",
        },
        "purpose": {
            "type": "string",
            "enum": ["personal", "group"],
            "description": "Purpose of the layered assistant. 'group' creates under group (astgr/ prefix), 'personal' or omitted creates personal (astr/ prefix).",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Display name of the layered assistant.",
        },
        "description": {
            "type": "string",
            "description": "Optional description of the layered assistant.",
        },
        "rootNode": {
            "type": "object",
            "description": "Root RouterNode of the layered assistant tree.",
            "properties": {
                "type": {"type": "string", "enum": ["router"]},
                "id":   {"type": "string"},
                "name": {"type": "string"},
                "description":  {"type": "string"},
                "instructions": {"type": "string"},
                "children": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["type", "id", "name", "children"],
        },
    },
    "required": ["name", "rootNode"],
}
