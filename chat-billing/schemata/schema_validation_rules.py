from .update_models_schema import update_models_schema

rules = {
    "validators": {
        "/available_models": {"read": {}},
        "/supported_models/update": {"update": update_models_schema},
        "/supported_models/get": {"read": {}},
        "/default_models": {"read": {}},
    },
    "api_validators": {
        "/available_models": {"read": {}},
        "/default_models": {"read": {}},
    },
}
