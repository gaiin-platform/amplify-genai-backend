integration_user_files_download_schema = {
    "download_file": {
        "type": "object",
        "properties": {
            "integration": {"type": "string"},
            "file_id": {"type": "string"},
            "direct_download": {"type": "boolean"},
        },
        "required": ["integration", "file_id"],
    }
}
