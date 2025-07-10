delete_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant",
        },
        "removePermsForUsers": {
            "type": "array",
            "description": "A list of user who have access to this ast",
            "items": {"type": "string"},
        },
    },
    "required": ["assistantId"],
}
