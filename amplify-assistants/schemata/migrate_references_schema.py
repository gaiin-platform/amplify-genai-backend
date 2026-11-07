migrate_references_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string", "description": "Public id of the assistant (astp/... or astgp/...)."},
        "replacements": {
            "type": "object",
            "description": "Document id stored in the assistant -> the id to store instead.",
            "minProperties": 1,
            "maxProperties": 200,
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["assistantId", "replacements"],
    "additionalProperties": False,
}
