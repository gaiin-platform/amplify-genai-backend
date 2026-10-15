add_project_file_schema = {
    "type": "object",
    "properties": {
        "projectId": {"type": "string", "maxLength": 200, "description": "The project the file belongs to"},
        "fileId": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "The file's key in the files service (<owner>/<date>/<uuid>.json)",
        },
        "name": {"type": "string", "minLength": 1, "maxLength": 300, "description": "Display name"},
        "type": {"type": "string", "maxLength": 200, "description": "MIME type"},
    },
    "required": ["projectId", "fileId", "name"],
}
