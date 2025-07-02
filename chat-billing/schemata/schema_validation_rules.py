from .report_generator_schema import report_generator_schema
from .update_models_schema import update_models_schema

rules = {
    "validators": {
        "/billing": {"report_generator": report_generator_schema},
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
