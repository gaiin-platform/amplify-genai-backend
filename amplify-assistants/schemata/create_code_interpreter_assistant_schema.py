create_code_interpreter_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The name of the item"},
        "description": {
            "type": "string",
            "description": "A brief description of the item",
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {"type": "string"},
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item",
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources keys",
            "items": {"type": "string"},
        },
    },
    "required": ["name", "description", "tags", "instructions", "dataSources"],
}
