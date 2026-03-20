delete_layered_assistant_schema = {
    "type": "object",
    "properties": {
        "publicId": {
            "type": "string",
            "minLength": 1,
            "description": "Public ID of the layered assistant to delete (astrp/<uuid>).",
        },
    },
    "required": ["publicId"],
}
