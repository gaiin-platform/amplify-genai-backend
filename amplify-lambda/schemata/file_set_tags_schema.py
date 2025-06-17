file_set_tags_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
    },
    "additionalProperties": False,
}
