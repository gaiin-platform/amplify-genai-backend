rag_migration_settings_schema = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["assistant", "all"]},
                "activeWithinDays": {"type": ["integer", "null"], "enum": [None, 30, 90, 180, 365]},
                "kinds": {"type": ["array", "null"], "items": {"type": "string", "enum": ["upload", "website", "drive"]}},
                "assistantTypes": {"type": ["array", "null"], "items": {"type": "string", "enum": ["personal", "group"]}},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        "batchSize": {"type": "integer", "minimum": 1, "maximum": 50},
        "maxInFlight": {"type": "integer", "minimum": 1, "maximum": 200},
        "maxDocs": {"type": ["integer", "null"], "minimum": 0},
        "autoCutover": {"type": "boolean"},
        "migrateOnAccess": {"type": "boolean"},
    },
    "additionalProperties": False,
}

rag_migration_action_schema = {
    "type": "object",
    "properties": {
        # Kept in step with ACTIONS in rag/migration_service.py (a test compares the two).
        "action": {
            "type": "string",
            "enum": [
                "migrate", "refresh_inventory", "retry_failed", "cutover", "rollback", "assistant_usage", "cleanup",
                "dismiss_error", "lookup", "rebuild", "preview_cleanup", "browse", "search", "clear_queue", "orphans", "remove_orphans",
                "estimate", "refresh_costs",
            ],
        },
        "keys": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 50},
        "all": {"type": "boolean"},
        "count": {"type": "integer", "minimum": 1, "maximum": 50},
        "category": {"type": "string", "enum": ["assistantGone", "drive", "website", "group"]},
        "confirm": {"type": "string", "maxLength": 20},
        "query": {"type": "string", "maxLength": 400},
        "state": {"type": "string", "enum": ["failed", "stuck", "processing", "starting", "completed", "failedChunks", "noStatus"]},
        "namespace": {"type": "string", "enum": ["global", "global2"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "start": {"type": ["string", "null"], "maxLength": 2000},
    },
    "required": ["action"],
    "additionalProperties": False,
}
