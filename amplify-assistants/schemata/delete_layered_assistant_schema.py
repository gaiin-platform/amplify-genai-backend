delete_layered_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "minLength": 1,
            "description": "ID of the layered assistant to delete (astr/<uuid> or astgr/<uuid>).",
        },
    },
    "required": ["assistantId"],
}
