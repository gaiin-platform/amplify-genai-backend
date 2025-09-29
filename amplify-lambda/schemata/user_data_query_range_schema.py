user_data_query_range_schema = {
    "query_by_range": {
        "type": "object",
        "required": ["appId", "entityType"],
        "properties": {
            "appId": {"type": "string"},
            "entityType": {"type": "string"},
            "rangeStart": {"type": "string"},
            "rangeEnd": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    }
}
