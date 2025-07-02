register_ops_schema = {
    "type": "object",
    "properties": {
        "system_op": {"type": "boolean"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "type": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "description"],
                            "additionalProperties": False,
                        },
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "method", "url", "name", "params"],
                "additionalProperties": True,
            },
        },
    },
    "required": ["ops"],
}
