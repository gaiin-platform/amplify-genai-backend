user_data_uuid_get_schema = {
    "get_by_uuid": {
        "type": "object",
        "required": ["uuid"],
        "properties": {"uuid": {"type": "string", "format": "uuid"}},
    }
}
