report_generator_schema = {
    "type": "object",
    "properties": {
        "emails": {
            "type": "array",
            "items": {"type": "string", "format": "email"},
            "description": "These are the emails you will collect usage data for.",
        },
    },
    "required": ["emails"],
}
