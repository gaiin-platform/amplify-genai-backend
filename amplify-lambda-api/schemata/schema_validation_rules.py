from .create_api_keys_schema import create_api_keys_schema
from .update_key_schema import update_key_schema
from .api_key_schema import api_key_schema
from .upload_api_doc_schema import upload_api_doc_schema
from .tools_op_schema import tools_op_schema

rules = {
    "validators": {
        "/apiKeys/key/deactivate": {"deactivate": api_key_schema},
        "/apiKeys/keys/create": {"create": create_api_keys_schema},
        "/apiKeys/keys/get": {"read": {}},
        "/apiKeys/key/rotate": {"rotate": api_key_schema},
        "/apiKeys/get_keys_ast": {"read": {}},
        "/apiKeys/keys/update": {"update": update_key_schema},
        "/apiKeys/get_system_ids": {"read": {}},
        "/apiKeys/api_documentation/get": {"read": {}},
        "/apiKeys/api_documentation/upload": {"upload": upload_api_doc_schema},
        "/apiKeys/api_documentation/get_templates": {"read": {}},
        "/apiKeys/register_ops": {"register_ops": tools_op_schema}, 
    },
    "api_validators": { 
        "/apiKeys/key/deactivate": {"deactivate": api_key_schema},
        "/apiKeys/get_system_ids": {"read": {}},
        "/apiKeys/register_ops": {"register_ops": tools_op_schema}, 
    },
}
