get_op_schema = {
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "The tag to search within."},
            "op_name": {"type": "string", "description": "The operation name/id to find."},
            "system_op": {"type": "boolean", "description": "Whether to search in system operations (default: false)."}
        },
        "required": ["tag", "op_name"],
    }