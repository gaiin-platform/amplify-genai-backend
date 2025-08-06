set_metadata_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "The unique id for the datasource item.",
        },
        "name": {"type": "string", "description": "The name of the data item."},
        "type": {"type": "string", "description": "The type of the data item."},
        "knowledge_base": {
            "type": "string",
            "description": "The knowledge base, default is 'default'.",
            "default": "default",
        },
        "data": {
            "type": "object",
            "description": "Additional properties for the data item.",
            "default": {},
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the data item.",
            "items": {"type": "string"},
            "default": [],
        },
    },
    "required": ["id", "name", "type"],
}
