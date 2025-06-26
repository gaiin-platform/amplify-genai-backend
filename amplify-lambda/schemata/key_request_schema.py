key_request_schema = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Key."},
        "groupId": {"type": "string", "description": "Group Id."},
    },
    "required": ["key"],
}
