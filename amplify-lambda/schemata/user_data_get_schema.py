user_data_get_schema = {
    "get_item": {
        "type": "object",
        "required": ["appId", "entityType", "itemId"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "itemId": {"type": "string"},
            "rangeKey": {"type": "string"},
        },
    }
}
