from .add_charge_schema import add_charge_schema
from .share_schema import share_schema
from .share_load_schema import share_load_schema
from .set_metadata_schema import set_metadata_schema
from .file_upload_schema import file_upload_schema
from .key_request_schema import key_request_schema
from .file_delete_schema import file_delete_schema
from .file_set_tags_schema import file_set_tags_schema
from .user_delete_tag_schema import user_delete_tag_schema
from .create_tags_schema import create_tags_schema
from .file_query_schema import file_query_schema
from .chat_input_schema import chat_input_schema
from .convert_schema import convert_schema
from .save_accounts_schema import save_accounts_schema
from .compressed_conversation_schema import compressed_conversation_schema
from .register_conversation_schema import register_conversation_schema
from .conversation_ids_schema import conversation_ids_schema
from .save_settings_schema import save_settings_schema
from .tools_op_schema import tools_op_schema

rules = {
    "validators": {
        "/state/share": {"append": share_schema, "read": {}},
        "/state/share/load": {"load": share_load_schema},
        "/datasource/metadata/set": {"set": set_metadata_schema},
        "/files/upload": {"upload": file_upload_schema},
        "/files/download": {"download": key_request_schema},
        "/files/delete": {"delete": file_delete_schema},
        "/files/set_tags": {"set_tags": file_set_tags_schema},
        "/files/tags/delete": {"delete": user_delete_tag_schema},
        "/files/tags/create": {"create": create_tags_schema},
        "/files/tags/list": {"list": {}},
        "/files/query": {"query": file_query_schema},
        "/chat/convert": {"convert": convert_schema},
        "/state/accounts/charge": {"create_charge": add_charge_schema},
        "/state/accounts/save": {"save": save_accounts_schema},
        "/state/accounts/get": {"get": {}},
        "/state/conversation/upload": {
            "conversation_upload": compressed_conversation_schema
        },
        "/state/conversation/register": {
            "conversation_upload": register_conversation_schema
        },
        "/state/conversation/get/multiple": {
            "get_multiple_conversations": conversation_ids_schema
        },
        "/state/conversation/get": {"read": {}},
        "/state/conversation/get/all": {"read": {}},
        "/state/conversation/get/empty": {"read": {}},
        "/state/conversation/get/metadata": {"read": {}},
        "/state/conversation/get/since/{timestamp}": {"read": {}},
        "/state/conversation/delete_multiple": {
            "delete_multiple_conversations": conversation_ids_schema
        },
        "/state/conversation/delete": {"delete": {}},
        "/chat": {"chat": chat_input_schema},
        "/state/settings/save": {"save": save_settings_schema},
        "/state/settings/get": {"get": {}},
        "/files/reprocess/rag": {"upload": key_request_schema},
        "/state/register_ops": {"register_ops": tools_op_schema}, 
    },
    "api_validators": {
        "/state/share": {"read": {}},
        "/state/share/load": {"load": share_load_schema},
        "/files/upload": {"upload": file_upload_schema},
        "/files/set_tags": {"set_tags": file_set_tags_schema},
        "/files/tags/delete": {"delete": user_delete_tag_schema},
        "/files/tags/create": {"create": create_tags_schema},
        "/files/tags/list": {"list": {}},
        "/files/query": {"query": file_query_schema},
        "/chat": {"chat": chat_input_schema},
        "/files/download": {"download": key_request_schema},
        "/state/conversation/register": {
            "conversation_upload": register_conversation_schema
        },
        "/state/accounts/get": {"get": {}},
        "/state/conversation/get/metadata": {"read": {}},
        "/state/conversation/get/since/{timestamp}": {"read": {}},
        "/files/reprocess/rag": {"upload": key_request_schema},
        "/files/delete": {"delete": file_delete_schema},
        "/state/register_ops": {"register_ops": tools_op_schema}, 
    },
}
