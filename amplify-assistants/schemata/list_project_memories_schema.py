list_project_memories_schema = {
    "type": "object",
    "properties": {
        "projectId": {
            "type": "string",
            "maxLength": 200,
            "description": "The id of the project to list memories for",
        },
    },
    "required": ["projectId"],
}
