artifact_schema = {
    "type": "object",
    "properties": {
        "artifactId": {
            "type": "string",
        },
        "version": {
            "type": "number",
        },
        "name": {
            "type": "string",
        },
        "type": {
            "type": "string",
        },
        "description": {
            "type": "string",
        },
        "contents": {"type": "array", "items": {"type": "number"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "createdAt": {
            "type": "string",
        },
    },
    "required": ["artifactId", "name", "version", "type", "createdAt"],
}
