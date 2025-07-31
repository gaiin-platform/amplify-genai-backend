from .update_models_schema import update_models_schema
from .tools_op_schema import tools_op_schema

rules = {
    "validators": {
        "/available_models": {"read": {}},
        "/supported_models/update": {"update": update_models_schema},
        "/supported_models/get": {"read": {}},
        "/default_models": {"read": {}},
        "/models/register_ops": {"register_ops": tools_op_schema}, 
    },
    "api_validators": {
        "/available_models": {"read": {}},
        "/default_models": {"read": {}},
        "/models/register_ops": {"register_ops": tools_op_schema}, 
    },
}
