get_group_conversations_data_schema = {
    "type": "object",
    "properties": {
        "conversationId": {
            "type": "string",
            "description": "The id of the conversation",
        },
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        },
    },
    "required": ["conversationId", "assistantId"],
}
