embedding_ids_schema = {
    "type": "object",
    "properties": {
        "dataSources": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of data source IDs to delete embeddings from.",
        }
    },
    "required": ["dataSources"],
}
