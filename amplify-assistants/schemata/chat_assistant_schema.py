chat_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string"},
        "accountId": {"type": "string"},
        "requestId": {"type": "string"},
        "threadId": {"type": ["string", "null"]},
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
                                    "threadId": {"type": "string"},
                                    "role": {"type": "string"},
                                    "textContent": {"type": "string"},
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "enum": [
                                                        "image_file",
                                                        "file",
                                                        "application/pdf",
                                                        "text/csv",
                                                        "image/png",
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
    "required": ["assistantId", "messages"],
}
