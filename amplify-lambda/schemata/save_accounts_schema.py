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
                        "type": ["object", "null"],
                        "properties": {
                            "rate": {
                                "description": "A numeric limit or null, validated per account by the save handler.",
                            },
                            "period": {"type": "string"},
                        },
                        "description": "Cost restriction using the API key",
                    },
                },
                "required": ["id", "name"],
            },
        },
        "expectedVersion": {
            "type": ["string", "null"],
            "description": "The version returned when the account list was loaded.",
        },
    },
    "required": ["accounts"],
}
