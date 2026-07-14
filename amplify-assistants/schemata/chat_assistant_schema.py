chat_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string"},
        "accountId": {"type": "string"},
        "requestId": {"type": "string"},
        "messages": {
            "anyOf": [
                {  # Messages through amplify
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "role": {"type": "string"},
                            "type": {"type": "string"},
                            "data": {"type": "object", "additionalProperties": True},
                            "codeInterpreterMessageData": {
                                "type": "object",
                                "properties": {
                                    "codeInterpreterRecordId": {"type": "string"},
                                    "role": {"type": "string"},
                                    "textContent": {"type": "string"},
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "enum": [
                                                        "application/pdf",
                                                        "text/csv",
                                                        "image/png",
                                                        "binary/octet-stream",
                                                    ]
                                                },
                                                "values": {
                                                    "type": "object",
                                                    "properties": {
                                                        # Base64-encoded file bytes, returned directly for
                                                        # inline rendering — generated files are not persisted
                                                        # to S3, so there is no file_key/presigned_url.
                                                        "data": {"type": "string"},
                                                        "file_name": {"type": "string"},
                                                        "file_size": {
                                                            "type": "integer"
                                                        },
                                                    },
                                                    "required": [
                                                        "data",
                                                        "file_name",
                                                    ],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "required": ["type", "values"],
                                        },
                                    },
                                },
                                "required": [],
                            },
                        },
                        "required": ["id", "content", "role"],
                    },
                },
                {  # messages from API
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "role": {"type": "string", "enum": ["user", "assistant"]},
                            "dataSourceIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "role"],
                    },
                },
            ]
        },
    },
    "required": ["assistantId", "messages"],
}
