register_conversation_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "content": {"type": "string"},
                    "data": {"type": ["object", "null"], "additionalProperties": True},
                },
                "required": ["role", "content", "data"],
            },
        },
        "tags": {"type": ["array", "null"], "items": {"type": "string"}},
        "date": {
            "type": "string",
            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        },
        "data": {"type": ["object", "null"], "additionalProperties": True},
    },
    "required": ["name", "messages"],
}
