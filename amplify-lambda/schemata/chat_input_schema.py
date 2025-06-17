chat_input_schema = {
    "type": "object",
    "properties": {
        "temperature": {"type": "number"},
        "model": {"type": "string"},
        "max_tokens": {"type": "integer"},
        "dataSources": {
            "type": "array",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "required": ["id"],
                        "additionalProperties": True,
                        "properties": {"id": {"type": "string"}},
                    },
                ]
            },
        },
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["role", "content"],
                "properties": {
                    "role": {"type": "string", "enum": ["system", "assistant", "user"]},
                    "content": {"type": "string"},
                    "type": {"type": "string", "enum": ["prompt"]},
                },
            },
        },
        "options": {
            "type": "object",
            "properties": {
                "dataSourceOptions": {"type": "object"},
                "ragOnly": {"type": "boolean"},
                "skipRag": {"type": "boolean"},
                "assistantId": {"type": "string"},
                "model": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                },
                "prompt": {"type": "string"},
            },
            "required": ["model"],
        },
    },
    "required": ["temperature", "max_tokens", "messages", "options"],
}
