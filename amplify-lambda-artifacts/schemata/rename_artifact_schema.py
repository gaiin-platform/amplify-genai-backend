rename_artifact_schema = {
    "type": "object",
    "properties": {
        "keys": {
            "type": "array",
            "description": "Artifact keys (one per version) to give the new name",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 100,
        },
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
    },
    "required": ["keys", "name"],
}
