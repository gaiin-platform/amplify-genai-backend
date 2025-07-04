from .export_schema import export_schema

convert_schema = {
    "type": "object",
    "properties": {
        "format": {
            "type": "string",
            "description": "The format to convert to docx|pptx",
        },
        "conversationHeader": {
            "type": "string",
            "description": "A markdown header to use for each conversation",
        },
        "messageHeader": {
            "type": "string",
            "description": "A markdown header to use for each message",
        },
        "userHeader": {
            "type": "string",
            "description": "A markdown header to use for each user message",
        },
        "assistantHeader": {
            "type": "string",
            "description": "A markdown header to use for each assistant message",
        },
        "content": export_schema,
    },
    "required": ["format", "content"],
}
