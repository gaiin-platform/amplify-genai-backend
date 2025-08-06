assistant_path_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string", "description": "The ID of the assistant"},
        "astPath": {
            "type": "string",
            "description": "The path to add to the assistant",
        },
        "isPublic": {
            "type": "boolean",
            "description": "assistant is public to all amplify users",
        },
        "accessTo": {
            "type": "object",
            "description": "list of amplify groups and users that can access the assistant",
            "properties": {
                "amplifyGroups": {"type": "array", "items": {"type": "string"}},
                "users": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["assistantId", "astPath", "isPublic"],
}
