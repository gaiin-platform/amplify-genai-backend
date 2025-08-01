from .artifact_schema import artifact_schema

share_artifact_schema = {
    "type": "object",
    "properties": {
        "shareWith": {
            "type": "array",
            "description": "A list of user emails",
            "items": {"type": "string"},
            "artifact": artifact_schema,
        }
    },
    "required": ["shareWith", "artifact"],
}
