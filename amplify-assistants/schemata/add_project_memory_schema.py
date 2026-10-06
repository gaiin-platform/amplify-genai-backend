add_project_memory_schema = {
    "type": "object",
    "properties": {
        "projectId": {
            "type": "string",
            "maxLength": 200,
            "description": "The id of the project this memory belongs to",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
            "description": "The memory's content, as a single stated fact",
        },
        "sourceConversationId": {
            "type": "string",
            "maxLength": 200,
            "description": "The id of the conversation this memory was extracted from, if any",
        },
        "status": {
            "type": "string",
            "enum": ["pending"],
            "description": "Extracted memories are always stored as pending; approval is a separate edit call",
        },
        "supersedesIds": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 5,
            "description": "Ids of existing memories this one corrects/replaces; they are retired only when this memory is later approved, never at suggestion time",
        },
    },
    "required": ["projectId", "content"],
}
