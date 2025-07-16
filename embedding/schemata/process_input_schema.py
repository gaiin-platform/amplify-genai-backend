process_input_schema = {
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
        "limit": {
            "type": "integer",
            "description": "The maximum number of documents to return.",
        },
    },
    "required": ["dataSources", "userInput"],
}
