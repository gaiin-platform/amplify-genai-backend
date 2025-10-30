rate_limit_schema = {
    "type": "object",
    "properties": {
        "period": {"type": "string"},
        "rate": { "type": ["number", "null"] }, 
    },
    "required": ["period", "rate"],
    "additionalProperties": False
}