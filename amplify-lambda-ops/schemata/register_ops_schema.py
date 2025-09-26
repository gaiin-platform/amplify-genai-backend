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
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "properties": {
                                "type": "object",
                                "patternProperties": {
                                    ".*": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"}
                                        },
                                        "required": ["type"],
                                        "additionalProperties": True
                                    }
                                }
                            },
                            "required": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["type", "properties"],
                        "additionalProperties": True
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "method", "url", "name"],
                "additionalProperties": True,
            },
        },
    },
    "required": ["ops"],
}
