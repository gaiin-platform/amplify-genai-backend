update_amplify_group_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "amplify_groups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["group_id", "amplify_groups"],
}
