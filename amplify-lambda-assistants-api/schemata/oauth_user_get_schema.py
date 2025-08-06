oauth_user_get_schema = {
    "get_user_oauth_token": {
        "type": "object",
        "properties": {
            "integration": {
                "type": "string",
                "description": "The name of the integration (e.g., google_sheets)",
            }
        },
        "required": ["integration"],
    }
}
