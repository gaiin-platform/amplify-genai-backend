from . import add_charge_schema
from . import share_schema
from . import share_load_schema
from . import set_metadata_schema
from . import file_upload_schema
from . import key_request_schema
from . import file_delete_schema
from . import file_set_tags_schema
from . import user_delete_tag_schema
from . import create_tags_schema
from . import file_query_schema
from . import chat_input_schema
from . import convert_schema
from . import save_accounts_schema
from . import compressed_conversation_schema
from . import register_conversation_schema
from . import conversation_ids_schema
from . import save_settings_schema

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
        "/state/conversation/get/metadata": {"read": {}},
        "/state/conversation/get/since/{timestamp}": {"read": {}},
        "/files/reprocess/rag": {"upload": key_request_schema},
    },
}
