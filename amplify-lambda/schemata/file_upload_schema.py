file_upload_schema = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "name": {"type": "string"},
        "knowledgeBase": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "data": {"type": "object"},
        "groupId": {"type": ["string", "null"]},
        "ragOn": {"type": "boolean"},
    },
    "required": ["type", "name", "knowledgeBase", "tags", "data"],
}
