dual_retrieval_schema = {
    "type": "object",
    "properties": {
        "userInput": {
            "type": "string",
            "description": "User input text for embedding and document retrieval.",
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources to search for related documents.",
        },
        "groupDataSources": {
            "type": "object",
            "description": "A dict of group data sources to search for related documents. Group is the key, list of globals is the value.",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "astDataSources": {
            "type": "object",
            "description": "A dict of ast data sources to search for related documents. Ast is the key, list of globals is the value.",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "limit": {
            "type": "integer",
            "description": "The maximum number of documents to return.",
        },
    },
    "required": ["dataSources", "userInput"],
}
