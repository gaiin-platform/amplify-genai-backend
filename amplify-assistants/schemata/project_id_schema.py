project_id_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "maxLength": 200,
            "description": "The id of the project",
        }
    },
    "required": ["id"],
}
