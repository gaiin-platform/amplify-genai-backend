from .sample_schema import sample_schema

rules = {"validators": {
                "/someservice/sample": {
                    "sample": sample_schema
                },
            }, 
            "api_validators": {}
            }
