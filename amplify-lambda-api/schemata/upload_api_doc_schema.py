upload_api_doc_schema = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
        },
        "content_md5": {
            "type": "string",
        },
    },
    "required": ["filename", "content_md5"],
}
