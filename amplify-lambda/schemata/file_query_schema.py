file_query_schema = {
    "type": "object",
    "properties": {
        "startDate": {
            "type": "string",
            "format": "date-time",
            "default": "2021-01-01T00:00:00Z",
        },
        "pageSize": {"type": "integer", "default": 10},
        "pageKey": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "createdAt": {"type": "string"},
                "type": {"type": "string"},
            },
        },
        "namePrefix": {"type": ["string", "null"]},
        "createdAtPrefix": {"type": ["string", "null"]},
        "typePrefix": {"type": ["string", "null"]},
        "types": {"type": "array", "items": {"type": "string"}, "default": []},
        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
        "pageIndex": {"type": "integer", "default": 0},
        "forwardScan": {"type": "boolean", "default": True},
        "sortIndex": {"type": "string", "default": "createdAt"},
    },
    "additionalProperties": False,
}
