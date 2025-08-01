update_groups_schema = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string"},
                    "amplifyGroups": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "isPublic": {"type": "boolean"},
                    "supportConvAnalysis": {"type": "boolean"},
                },
                "required": [
                    "group_id",
                    "amplifyGroups",
                    "isPublic",
                    "supportConvAnalysis",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": [],
}
