user_data_put_schema = {
    "put_item": {
        "type": "object",
        "required": ["appId", "entityType", "itemId", "data"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "itemId": {"type": "string"},
            "data": {"type": "object"},
            "rangeKey": {"type": "string"},
        },
    }
}
