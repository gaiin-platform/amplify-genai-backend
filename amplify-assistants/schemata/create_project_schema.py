create_project_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "The name of the project",
        },
        "description": {
            "type": "string",
            "maxLength": 2000,
            "description": "A brief description of the project",
        },
        "instructions": {
            "type": "string",
            "maxLength": 12000,
            "description": "Custom instructions applied to every chat started inside this project",
        },
        "assistantId": {
            "type": "string",
            "maxLength": 200,
            "description": "Default assistant applied to new chats in this project ('' clears it)",
        },
        "assistantName": {
            "type": "string",
            "maxLength": 200,
            "description": "Display name of the default assistant",
        },
        "memoryEnabled": {
            "type": "boolean",
            "description": "Whether the assistant should extract and use project-scoped memory for this project",
        },
    },
    "required": ["name"],
}
