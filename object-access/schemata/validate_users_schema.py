validate_users_schema = {
    "type": "object",
    "properties": {
        "user_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "An array of user names (email addresses) to validate as Amplify users.",
        }
    },
    "required": ["user_names"],
}
