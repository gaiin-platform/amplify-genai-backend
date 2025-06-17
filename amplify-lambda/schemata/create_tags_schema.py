create_tags_schema = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}, "default": []}
    },
    "additionalProperties": False,
}
