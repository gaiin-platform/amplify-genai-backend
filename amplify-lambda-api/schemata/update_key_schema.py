update_key_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "apiKeyId": {"type": "string", "description": "API key id string"},
            "updates": {
                "type": "object",
                "properties": {
                    "account": {
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
                        },
                        "required": ["id", "name"],
                    },
                    "rateLimit": {
                        "type": "object",
                        "properties": {
                            "rate": {"type": ["number", "null"]},
                            "period": {"type": "string"},
                        },
                        "description": "Cost restriction using the API key",
                    },
                    "expirationDate": {
                        "type": ["string", "null"],
                        "description": "The expiration date of the API key",
                    },
                    "accessTypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Types of access permitted by this API key",
                    },
                },
                "required": [],
            },
        },
        "required": ["apiKeyId", "updates"],
    },
}
