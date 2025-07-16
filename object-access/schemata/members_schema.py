members_schema = {
    "type": "object",
    "patternProperties": {
        ".*": {  # This regex matches any string as the property name
            "type": "string",
            "enum": ["write", "read", "admin"],
        }
    },
}
