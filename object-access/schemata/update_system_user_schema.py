update_system_user_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "system_users": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["group_id", "system_users"],
}
