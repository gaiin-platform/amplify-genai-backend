rate_limit_schema = {
    "type": "object",
    "properties": {
        "period": {"type": "string"},
        "rate": { "type": ["number", "null"] },
    },
    "required": ["period", "rate"],
    "additionalProperties": False
}

# Accepts either a single rate limit object (backward compat) or an array of limits
rate_limits_schema = {
    "oneOf": [
        rate_limit_schema,
        {
            "type": "array",
            "items": rate_limit_schema,
            "minItems": 1
        }
    ]
}

# honorPersonalRateLimit config object
# enabled: whether to honor personal limits at all
# scope: which access type to honor personal limits for
#   "both"           - honor for both API key and Amplify web account users
#   "apiKey"         - honor only for API key users
#   "amplifyAccount" - honor only for Amplify web account users
honor_personal_rate_limit_schema = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "scope": {"type": "string", "enum": ["both", "apiKey", "amplifyAccount"]}
    },
    "required": ["enabled"],
    "additionalProperties": False
}

# Full admin rate limit config: wraps limits array + honorPersonalRateLimit config.
# Also accepts the legacy flat rate_limits_schema shape for backward compatibility.
admin_rate_limit_config_schema = {
    "oneOf": [
        # New shape: { limits: [...], honorPersonalRateLimit: { enabled, scope } }
        {
            "type": "object",
            "properties": {
                "limits": rate_limits_schema,
                "honorPersonalRateLimit": honor_personal_rate_limit_schema
            },
            "required": ["limits"],
            "additionalProperties": False
        },
        # Legacy shape: single limit object or array (no wrapper)
        rate_limits_schema
    ]
}