simulate_access_to_objects = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        }
    },
    "required": ["objects"],
}
