rescan_websites_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        },
        "forceRescan": {
            "type": "boolean",
            "description": "If true, will force a rescan of all websites regardless of scan frequency.",
            "default": False
        }
    },
    "required": ["assistantId"],
}
