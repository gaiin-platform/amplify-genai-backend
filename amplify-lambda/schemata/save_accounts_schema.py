save_accounts_schema = {
    "type": "object",
    "properties": {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "A unique identifier for the account.",
                    },
                    "name": {
                        "type": "string",
                        "description": "The name of the account.",
                    },
                    "isDefault": {
                        "type": "boolean",
                        "description": "Indicates if this is the default account.",
                    },
                    "rateLimit": {
                        "type": "object",
                        "properties": {
                            "rate": {"type": ["number", "null"]},
                            "period": {"type": "string"},
                        },
                        "description": "Cost restriction using the API key",
                    },
                },
                "required": ["id", "name", "rateLimit"],
            },
        }
    },
    "required": ["accounts"],
}
