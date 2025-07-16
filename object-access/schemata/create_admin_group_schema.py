from .members_schema import members_schema

create_admin_group_schema = {
    "type": "object",
    "properties": {
        "group_name": {
            "type": "string",
            "description": "The name of the group to be created.",
        },
        "members": members_schema,
        "types": {"type": "array", "items": {"type": "string"}},
        "amplify_groups": {"type": "array", "items": {"type": "string"}},
        "system_users": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["group_name", "members"],
}
