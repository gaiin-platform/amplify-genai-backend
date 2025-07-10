remove_astp_perms_schema = {
    "type": "object",
    "properties": {
        "assistant_public_id": {"type": "string", "description": "astp assistantId."},
        "users": {
            "type": "array",
            "description": "Remove astp permissions for each user",
            "items": {"type": "string"},
        },
    },
    "required": ["assistant_public_id", "users"],
}
