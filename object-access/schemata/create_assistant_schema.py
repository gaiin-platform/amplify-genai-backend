create_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The name of the item"},
        "description": {
            "type": "string",
            "description": "A brief description of the item",
        },
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant",
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
        "disclaimer": {
            "type": "string",
            "description": "Appended assistant response disclaimer related to the item",
        },
        "uri": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "The endpoint that receives requests for the assistant",
                },
                {"type": "null"},
            ]
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The key of the data source",
                    }
                },
            },
        },
    },
    "required": ["name", "description", "tags", "instructions", "dataSources"],
}
