conversation_ids_schema = {
    "type": "object",
    "properties": {
        "conversationIds": {
            "type": "array",
            "items": {
                "type": "string",
            },
        }
    },
    "required": ["conversationIds"],
}
