save_user_rating_schema = {
    "type": "object",
    "properties": {
        "conversationId": {
            "type": "string",
            "description": "The id of the conversation",
        },
        "userRating": {
            "type": "number",
            "description": "The user's rating",
        },
        "userFeedback": {
            "type": "string",
            "description": "Optional user feedback on the conversation",
        },
    },
    "required": ["conversationId", "userRating"],
}
