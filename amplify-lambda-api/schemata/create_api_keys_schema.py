create_api_keys_schema = {
    "type": "object",
    "properties": {
        "owner": {"type": "string", "description": "The owner of the API key"},
        "account": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "A unique identifier for the account.",
                },
                "name": {"type": "string", "description": "The name of the account."},
                "isDefault": {
                    "type": "boolean",
                    "description": "Indicates if this is the default account.",
                },
            },
            "required": ["id", "name"],
        },
        "delegate": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "Optional delegate responsible for the API key",
                },
                {"type": "null"},
            ]
        },
        "appName": {
            "type": "string",
            "description": "The name of the application using the API key",
        },
        "appDescription": {
            "type": "string",
            "description": "A description of the application using the API key",
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
        "systemUse": {"type": "boolean", "description": "For system use"},
        "purpose": {"type": "string", "description": "The purpose of the API key"},
    },
    "required": ["owner", "appName", "account", "accessTypes", "rateLimit"],
}
