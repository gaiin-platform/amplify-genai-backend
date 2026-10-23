edit_project_memory_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "maxLength": 200,
            "description": "The id of the memory to edit",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
            "description": "The memory's new content",
        },
        "status": {
            "type": "string",
            "enum": ["pending", "approved"],
        },
    },
    "required": ["id", "content"],
}
