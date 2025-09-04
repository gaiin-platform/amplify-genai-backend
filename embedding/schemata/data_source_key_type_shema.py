data_source_key_type_schema = {
    "type": "object",
    "properties": {
        "dataSources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The data source key/ID"
                    },
                    "type": {
                        "type": "string", 
                        "description": "The data source type (e.g., text/plain, image/png, etc.)"
                    }
                },
                "required": ["key", "type"]
            },
            "description": "List of data sources with key and type information"
        }
    },
    "required": ["dataSources"]
}