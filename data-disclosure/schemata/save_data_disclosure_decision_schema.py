# JSON schema for validating the request to save a user's data disclosure decision
save_data_disclosure_decision_schema = {
    "type": "object",
    "properties": {
        "email": {
            "type": "string",
            "format": "email",
            "description": "The email of the user to save the data disclosure decision for.",
        },
        "acceptedDataDisclosure": {
            "type": "boolean",
            "description": "The decision of the user regarding the data disclosure.",
        },
    },
    "required": ["email", "acceptedDataDisclosure"],
}
