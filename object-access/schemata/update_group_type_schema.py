update_group_type_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "types": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["group_id", "types"],
}
