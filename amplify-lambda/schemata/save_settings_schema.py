save_settings_schema = {
    "type": "object",
    "properties": {
        "settings": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "enum": ["light", "dark"]},
                "featureOptions": {
                    "type": "object",
                    "additionalProperties": {"type": "boolean"},
                },
                "hiddenModelIds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },
            "required": ["theme", "featureOptions", "hiddenModelIds"],
        }
    },
    "required": ["settings"],
}
