# JSON schema for validating the request to save a user's data disclosure decision
save_data_disclosure_decision_schema = {
    "type": "object",
    "properties": {
        "acceptedDataDisclosure": {
            "type": "boolean",
            "description": "The decision of the user regarding the data disclosure.",
        },
    },
    "required": ["acceptedDataDisclosure"],
}
