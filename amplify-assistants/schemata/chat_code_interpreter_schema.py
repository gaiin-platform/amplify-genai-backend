chat_code_interpreter_schema = {
    "type": "object",
    "properties": {
        "codeInterpreterRecordId": {"type": "string"},
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
                                                        "file_key": {"type": "string"},
                                                        "presigned_url": {
                                                            "type": "string"
                                                        },
                                                        "file_key_low_res": {
                                                            "type": "string"
                                                        },
                                                        "presigned_url_low_res": {
                                                            "type": "string"
                                                        },
                                                        "file_size": {
                                                            "type": "integer"
                                                        },
                                                    },
                                                    "required": [
                                                        "file_key",
                                                        "presigned_url",
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
    "required": ["codeInterpreterRecordId", "messages"],
}
