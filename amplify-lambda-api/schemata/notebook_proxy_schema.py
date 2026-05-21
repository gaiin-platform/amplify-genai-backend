"""Schema definitions for the notebook proxy endpoints."""

notebook_proxy_schema = {
    "type": "object",
    "properties": {
        "method": {
            "type": "string",
            "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
        },
        "path": {
            "type": "string",
            "minLength": 1,
        },
        "query_params": {
            "type": "object",
        },
        "body": {},  # any type — forwarded as-is to Open Notebook
    },
    "required": ["path"],
}

notebook_upload_schema = {
    "type": "object",
    "properties": {
        "body_b64": {
            "type": "string",
            "minLength": 1,
        },
        "content_type": {
            "type": "string",
            "minLength": 1,
        },
    },
    "required": ["body_b64", "content_type"],
}
