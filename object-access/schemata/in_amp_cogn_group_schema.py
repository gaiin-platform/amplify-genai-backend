in_amp_cogn_group_schema = {
    "type": "object",
    "properties": {
        "amplifyGroups": {"type": "array", "items": {"type": "string"}},
        "cognitoGroups": {"type": "array", "items": {"type": "string"}},
    },
    "anyOf": [{"required": ["amplifyGroups"]}, {"required": ["cognitoGroups"]}],
}
