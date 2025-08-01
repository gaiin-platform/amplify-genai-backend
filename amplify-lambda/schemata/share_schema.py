from .export_schema import export_schema

share_schema = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "sharedWith": {"type": "array", "items": {"type": "string"}},
        "sharedData": export_schema,
    },
    "required": ["sharedWith", "sharedData", "note"],
}
