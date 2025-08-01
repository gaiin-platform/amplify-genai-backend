upload_pptx_schema = {
    "type": "object",
    "properties": {
        "fileName": {"type": "string"},
        "isAvailable": {"type": "boolean"},
        "amplifyGroups": {"type": "array", "items": {"type": "string"}},
        "contentType": {"type": "string"},
        "md5": {"type": "string"},
    },
    "required": ["fileName", "isAvailable", "amplifyGroups", "contentType", "md5"],
}
