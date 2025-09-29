user_data_delete_schema = {
    "delete_item": {
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
