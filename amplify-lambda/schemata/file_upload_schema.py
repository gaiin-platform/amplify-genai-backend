file_upload_schema = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "saveAsData",
                            "createChunks",
                            "ingestRag",
                            "makeDownloadable",
                            "extractText",
                        ],
                    },
                    "params": {"type": "object", "additionalProperties": True},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "type": {"type": "string"},
        "name": {"type": "string"},
        "knowledgeBase": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "data": {"type": "object"},
        "groupId": {"type": ["string", "null"]},
        "ragOn": {"type": "boolean"},
    },
    "required": ["type", "name", "knowledgeBase", "tags", "data"],
}
