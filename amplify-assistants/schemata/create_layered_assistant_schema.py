
create_layered_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "Existing layered assistant ID to update (astr/<uuid> or astgr/<uuid>). Omit to create.",
        },
        "purpose": {
            "type": "string",
            "enum": ["personal", "group"],
            "description": "Purpose of the layered assistant. 'group' creates under group (astgr/ prefix), 'personal' or omitted creates personal (astr/ prefix).",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Display name of the layered assistant.",
        },
        "description": {
            "type": "string",
            "description": "Optional description of the layered assistant.",
        },
        "rootNode": {
            "type": "object",
            "description": "Root RouterNode of the layered assistant tree.",
            "properties": {
                "type": {"type": "string", "enum": ["router"]},
                "id":   {"type": "string"},
                "name": {"type": "string"},
                "description":  {"type": "string"},
                "instructions": {"type": "string"},
                "children": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["type", "id", "name", "children"],
        },
        "trackConversations": {
            "type": "boolean",
            "description": "Whether to record conversations under this layered assistant's assistantId.",
        },
        "supportConvAnalysis": {
            "type": "boolean",
            "description": "Whether to run AI analysis (systemRating + optional category) on tracked conversations.",
        },
        "analysisCategories": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Category list for AI conversation analysis. Only used when supportConvAnalysis is true.",
        },
    },
    "required": ["name", "rootNode"],
}
