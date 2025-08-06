from .assistant_path_schema import assistant_path_schema

add_assistant_path_schema = {
    "type": "object",
    "properties": {
        "group_id": {"type": "string", "description": "The ID of the group."},
        "path_data": assistant_path_schema,
    },
    "required": ["group_id", "path_data"],
}
