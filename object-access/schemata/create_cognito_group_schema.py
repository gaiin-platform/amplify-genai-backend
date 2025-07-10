create_cognito_group_schema = {
    "type": "object",
    "properties": {
        "groupName": {
            "type": "string",
            "description": "The name of the group to create.",
        },
        "groupDescription": {
            "type": "string",
            "description": "The description of the group to create.",
        },
    },
    "required": ["groupName", "groupDescription"],
}
