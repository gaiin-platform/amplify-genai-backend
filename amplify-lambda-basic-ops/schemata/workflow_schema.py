workflow_schema = {
    "type": "object",
    "properties": {"workflow": {"type": "string"}, "context": {"type": "object"}},
    "required": ["workflow"],
    "additionalProperties": True,
}
