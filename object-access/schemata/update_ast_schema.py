from .create_assistant_schema import create_assistant_schema

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
                {"type": "array", "items": create_assistant_schema},
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "astp assistantId for REMOVE",
                    },
                },
            ]
        },
    },
    "required": ["group_id", "update_type", "assistants"],
}
