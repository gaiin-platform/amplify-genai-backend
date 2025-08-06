execute_custom_auto_schema = {
    "type": "object",
    "properties": {
        "action": {"type": "object"},
        "conversation": {"type": "string"},
        "assistant": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["action", "message", "conversation", "assistant"],
}
