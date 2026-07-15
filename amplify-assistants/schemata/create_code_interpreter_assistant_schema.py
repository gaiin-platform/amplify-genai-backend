create_code_interpreter_assistant_schema = {
    "type": "object",
    "properties": {
        "dataSources": {
            "type": "array",
            "description": "A list of data source keys to load into the session",
            "items": {"type": "string"},
        },
        "fileNames": {
            "type": "object",
            "description": "Map of data source key -> original filename, since S3 keys are random UUIDs.",
            "additionalProperties": {"type": "string"},
        },
    },
    "required": [],
}
