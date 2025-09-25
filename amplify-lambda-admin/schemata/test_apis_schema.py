test_apis_schema = {
    "type": "object",
    "properties": {
        "services": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of specific services to test"
        }
    }
}