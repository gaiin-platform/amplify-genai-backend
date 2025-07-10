from .members_schema import members_schema

update_members_perms_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "affected_members": members_schema,
    },
    "required": ["group_id", "affected_members"],
}
