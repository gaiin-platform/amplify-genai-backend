query_schema = {
    "title": "Data Schema",
    "type": "object",
    "properties": {
        "query": {"type": "string"},
    },
    "required": ["query", "id"],
    "additionalProperties": True,
}
