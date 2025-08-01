user_data_batch_get_schema = {
    "batch_get_items": {
        "type": "object",
        "required": ["appId", "entityType", "itemIds"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "itemIds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {
                        "itemId": {"type": "string"},
                        "rangeKey": {"type": "string"},
                    },
                },
            },
        },
    }
}
