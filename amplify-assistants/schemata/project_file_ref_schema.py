project_file_ref_schema = {
    "type": "object",
    "properties": {
        "projectId": {"type": "string", "maxLength": 200},
        "fileId": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["projectId", "fileId"],
}
