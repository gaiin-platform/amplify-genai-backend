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