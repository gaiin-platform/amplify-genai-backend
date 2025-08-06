oauth_user_refresh_token_schema = {
    "refresh_token": {
        "type": "object",
        "properties": {"integration": {"type": "string"}},
        "required": ["integration"],
        "additionalProperties": False,
    }
}
