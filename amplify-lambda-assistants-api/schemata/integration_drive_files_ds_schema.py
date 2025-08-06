integration_drive_files_ds_schema = {
    "upload_files": {
        "type": "object",
        "patternProperties": {
            "^(google|microsoft|.*?)$": {
                "type": "object",
                "properties": {
                    "folders": {
                        "type": "object",
                        "patternProperties": {
                            ".*": {
                                "type": "object",
                                "patternProperties": {
                                    ".*": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"},
                                            "lastCaptured": {"type": ["string", "null"], "format": "date-time"},
                                            "datasource": {
                                                "type": ["object", "null"],
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "name": {"type": "string"},
                                                    "raw": {"type": ["object", "null"]},
                                                    "type": {"type": "string"},
                                                    "data": {"type": ["object", "null"]},
                                                    "key": {"type": "string"},
                                                    "metadata": {"type": "object"},
                                                    "groupId": {"type": ["string", "null"]}
                                                },
                                                "required": ["id", "name", "type"]
                                            }
                                        },
                                        "required": ["type"]
                                    }
                                }
                            }
                        }
                    },
                    "files": {
                        "type": "object",
                        "patternProperties": {
                            ".*": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "lastCaptured": {"type": ["string", "null"], "format": "date-time"},
                                    "datasource": {
                                        "type": ["object", "null"],
                                        "properties": {
                                            "id": {"type": "string"},
                                            "name": {"type": "string"},
                                            "raw": {"type": ["object", "null"]},
                                            "type": {"type": "string"},
                                            "data": {"type": ["object", "null"]},
                                            "key": {"type": "string"},
                                            "metadata": {"type": "object"},
                                            "groupId": {"type": ["string", "null"]}
                                        },
                                        "required": ["id", "name", "type"]
                                    }
                                },
                                "required": ["type"]
                            }
                        }
                    }
                },
                "required": ["folders", "files"]
            }
        }
    }
}
