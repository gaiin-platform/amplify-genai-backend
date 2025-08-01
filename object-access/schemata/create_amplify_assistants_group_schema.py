from .create_assistant_schema import create_assistant_schema

create_amplify_assistants_group_schema = {
    "type": "object",
    "properties": {
        "assistants": {"type": "array", "items": create_assistant_schema},
        "members": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assistants", "members"],
}
