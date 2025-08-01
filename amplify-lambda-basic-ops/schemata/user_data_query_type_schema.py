user_data_query_type_schema = {
    "query_by_type": {
        "type": "object",
        "required": ["appId", "entityType"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    }
}
