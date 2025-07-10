delete_op_schema = {
    "type": "object",
    "properties": {
        "op": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["id", "url", "name", "tags"],
            "additionalProperties": False,
        }
    },
    "required": ["op"],
    "additionalProperties": False,
}
