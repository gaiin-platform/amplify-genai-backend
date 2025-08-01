share_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "Code interpreter Assistant Id",
        },
        "recipientUsers": {"type": "array", "items": {"type": "string"}},
        "accessType": {"type": "string"},
        "policy": {"type": "string", "default": ""},
        "note": {"type": "string"},
        "shareToS3": {"type": "boolean"},
    },
    "required": ["assistantId", "recipientUsers"],
    "additionalProperties": False,
}
