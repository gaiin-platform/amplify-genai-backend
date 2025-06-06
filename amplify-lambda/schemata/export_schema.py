export_schema = {
    "type": "object",
    "properties": {
        "version": {"type": "number"},
        "history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                    "compressedMessages": {
                        "type": ["array", "null"],
                        "items": {"type": "number"},
                    },
                    "model": {"type": "object"},
                    "prompt": {"type": ["string", "null"]},
                    "temperature": {"type": ["number", "null"]},
                    "folderId": {"type": ["string", "null"]},
                    "promptTemplate": {"type": ["object", "null"]},
                    "tags": {"type": ["array", "null"], "items": {"type": "string"}},
                    "maxTokens": {"type": ["number", "null"]},
                    "workflowDefinition": {"type": ["object", "null"]},
                    "data": {"type": ["object", "null"], "additionalProperties": True},
                    "codeInterpreterAssistantId": {"type": ["string", "null"]},
                    "isLocal": {"type": ["boolean", "null"]},
                },
                "required": ["id", "name", "messages", "model", "folderId"],
            },
        },
        "folders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["chat", "workflow", "prompt"]},
                },
                "required": ["id", "name", "type"],
            },
        },
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "content": {"type": "string"},
                    "model": {"type": ["object", "null"]},
                    "folderId": {"type": ["string", "null"]},
                    "type": {"type": ["string", "null"]},
                    "data": {
                        "type": "object",
                        "properties": {
                            "rootPromptId": {"type": ["string", "null"]},
                            "code": {"type": ["string", "null"]},
                        },
                        "additionalProperties": True,
                    },
                },
                "required": [
                    "id",
                    "name",
                    "description",
                    "content",
                    "folderId",
                    "type",
                ],
            },
            "required": ["version", "history", "folders", "prompts"],
        },
    },
}
