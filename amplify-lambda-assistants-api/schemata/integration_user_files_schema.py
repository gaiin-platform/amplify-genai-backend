integration_user_files_schema = {
    "list_files": {
        "type": "object",
        "properties": {
            "integration": {"type": "string"},
            "folder_id": {"type": "string"},
        },
        "required": ["integration"],
    }
}
