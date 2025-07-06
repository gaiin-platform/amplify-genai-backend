get_group_assistant_dashboards_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        },
        "startDate": {
            "type": "string",
            "format": "date-time",
            "description": "Optional start date for filtering conversations",
        },
        "endDate": {
            "type": "string",
            "format": "date-time",
            "description": "Optional end date for filtering conversations",
        },
        "includeConversationData": {
            "type": "boolean",
            "description": "Whether to include conversation data in CSV format",
        },
        "includeConversationContent": {
            "type": "boolean",
            "description": "Whether to include full conversation content",
        },
    },
    "required": ["assistantId"],
}
