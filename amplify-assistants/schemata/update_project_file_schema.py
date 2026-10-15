update_project_file_schema = {
    "type": "object",
    "properties": {
        "projectId": {"type": "string", "maxLength": 200},
        "fileId": {"type": "string", "minLength": 1, "maxLength": 500},
        "status": {"type": "string", "enum": ["processing", "ready", "failed"]},
        "totalTokens": {"type": "integer", "minimum": 0, "maximum": 100000000},
    },
    "required": ["projectId", "fileId"],
}
