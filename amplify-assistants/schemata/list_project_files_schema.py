list_project_files_schema = {
    "type": "object",
    "properties": {
        "projectId": {"type": "string", "maxLength": 200, "description": "The project to list files for"},
    },
    "required": ["projectId"],
}
