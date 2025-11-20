user_data_query_prefix_schema = {
    "query_by_prefix": {
        "type": "object",
        "required": ["appId", "entityType", "prefix"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "prefix": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    }
}
