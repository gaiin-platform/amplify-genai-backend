compressed_conversation_schema = {
    "type": "object",
    "properties": {
        "conversation": {"type": "array"},
        "conversationId": {
            "type": "string",
        },
        "folder": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "date": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["chat", "prompt", "workflow"],
                        },
                    },
                    "required": ["id", "name", "type"],
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["conversation", "conversationId"],
}
