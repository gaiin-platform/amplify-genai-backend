job_set_result_schema = {
    "set_result": {
        "type": "object",
        "properties": {
            "jobId": {"type": "string"},
            "result": {"type": "object"},
            "storeAsBlob": {"type": "boolean"},
        },
        "required": ["jobId", "result"],
        "additionalProperties": True,
    }
}
