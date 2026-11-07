legacy_cleanup_schema = {
    "type": "object",
    "properties": {
        "src_ids": {
            "type": "array",
            "description": "Legacy content keys (global/<hash>.content.json) whose old PostgreSQL rows should be deleted.",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 50,
        },
    },
    "required": ["src_ids"],
    "additionalProperties": False,
}
