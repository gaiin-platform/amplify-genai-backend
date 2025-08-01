from .members_schema import members_schema

update_members_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "update_type": {
            "type": "string",
            "enum": ["ADD", "REMOVE"],
            "description": "Type of update to perform on members.",
        },
        "members": {
            "anyOf": [
                members_schema,
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "member emails to  REMOVE ",
                    },
                },
            ]
        },
    },
    "required": ["group_id", "update_type", "members"],
}
