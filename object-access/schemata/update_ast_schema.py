from .create_assistant_schema import create_assistant_schema

_layered_assistant_wrapper_schema = {
    "type": "object",
    "properties": {
        "isLayeredAssistant": {
            "type": "boolean",
            "const": True,
            "description": "Discriminator flag — must be true for layered assistants.",
        },
        "layeredAssistant": {
            "type": "object",
            "properties": {
                "assistantId":         {"type": "string",  "description": "astgr/<uuid> — omit or empty to create new"},
                "name":                {"type": "string",  "description": "Display name of the layered assistant"},
                "description":         {"type": "string",  "description": "Optional description"},
                "rootNode":            {"type": "object",  "description": "Root RouterNode tree"},
                "trackConversations":  {"type": "boolean", "description": "Record conversations under this LA's assistantId"},
                "supportConvAnalysis": {"type": "boolean", "description": "Run AI analysis on tracked conversations"},
                "analysisCategories":  {"type": "array", "items": {"type": "string"}, "description": "Category list for AI analysis"},
            },
            "required": ["name", "rootNode"],
        },
    },
    "required": ["isLayeredAssistant", "layeredAssistant"],
}

update_ast_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "update_type": {
            "type": "string",
            "enum": ["ADD", "REMOVE", "UPDATE"],
            "description": "Type of update to perform on assistants.",
        },
        "assistants": {
            "oneOf": [
                {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            create_assistant_schema,
                            _layered_assistant_wrapper_schema,
                        ]
                    },
                },
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "astp assistantId for REMOVE (regular or astgr/ layered)",
                    },
                },
            ]
        },
    },
    "required": ["group_id", "update_type", "assistants"],
}
