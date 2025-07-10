oauth_register_secret_schema = {
    "register_secret": {
        "type": "object",
        "properties": {
            "client_id": {"type": "string"},
            "client_secret": {"type": "string"},
            "integration": {"type": "string"},
            "tenant_id": {"type": "string"},
        },
        "required": ["client_id", "client_secret", "integration"],
        "additionalProperties": False,
    }
}
