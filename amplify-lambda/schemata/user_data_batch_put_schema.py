user_data_batch_put_schema = {
    "batch_put_items": {
        "type": "object",
        "required": ["appId", "entityType", "items"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["itemId", "data"],
                    "properties": {
                        "itemId": {"type": "string"},
                        "rangeKey": {"type": "string"},
                        "data": {"type": "object"},
                    },
                },
            },
        },
    }
}
