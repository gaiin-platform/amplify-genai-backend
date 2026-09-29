start_schema = {
    "type": "object",
    "properties": {
        "templateName": {"type": "string", "minLength": 1, "maxLength": 256},
        "content": {"type": "string", "minLength": 1},
        "title": {"type": "string", "maxLength": 300},
        "instructions": {"type": "string", "maxLength": 4000},
        "conversationId": {"type": "string", "maxLength": 200},
        "accountId": {"type": "string", "maxLength": 200},
    },
    "required": ["templateName", "content"],
    "additionalProperties": False,
}

analyze_schema = {
    "type": "object",
    "properties": {
        "templateName": {"type": "string", "minLength": 1, "maxLength": 256},
    },
    "required": ["templateName"],
    "additionalProperties": False,
}

rules = {
    "validators": {
        "/presentation/start": {"start": start_schema},
        "/presentation/status": {"status": {}},
        "/presentation/template/analyze": {"analyze": analyze_schema},
        "/presentation/template/status": {"read": {}},
    },
    "api_validators": {},
}
