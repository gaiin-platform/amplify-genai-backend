upload_disclosure_schema = {
    "type": "object",
    "properties": {
        "fileName": {
            "type": "string",
        },
        "contentType": {
            "type": "string",
        },
        "md5": {
            "type": "string",
        },
    },
    "required": ["md5", "contentType", "fileName"],
}
