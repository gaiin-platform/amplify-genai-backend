from .delete_assistant_schema import delete_assistant_schema
from .lookup_assistant_schema import lookup_assistant_schema
from .add_assistant_path_schema import add_assistant_path_schema
from .create_assistant_schema import create_assistant_schema
from .create_code_interpreter_assistant_schema import create_code_interpreter_assistant_schema
from .share_assistant_schema import share_assistant_schema
from .chat_assistant_schema import chat_assistant_schema
from .download_ci_files_schema import download_ci_files_schema
from .remove_astp_perms_schema import remove_astp_perms_schema
from .assistant_id_schema import assistant_id_schema
from .get_group_assistant_dashboards_schema import get_group_assistant_dashboards_schema
from .save_user_rating_schema import save_user_rating_schema
from .get_group_conversations_data_schema import get_group_conversations_data_schema
from .scrape_website_schema import scrape_website_schema
from .tools_op_schema import tools_op_schema
from .rescan_websites_schema import rescan_websites_schema
from .extract_sitemap_urls_schema import extract_sitemap_urls_schema

rules = {
    "validators": {
        "/assistant/create": {"create": create_assistant_schema},
        "/assistant/delete": {"delete": delete_assistant_schema},
        "/assistant/share": {"share_assistant": share_assistant_schema},
        "/assistant/list": {"list": {}},  # Get
        "/assistant/chat/codeinterpreter": {"chat": chat_assistant_schema},
        "/assistant/create/codeinterpreter": {
            "create": create_code_interpreter_assistant_schema
        },
        "/assistant/files/download/codeinterpreter": {
            "download": download_ci_files_schema
        },
        "/assistant/openai/thread/delete": {"delete": {}},
        "/assistant/openai/delete": {"delete": {}},
        "/assistant/remove_astp_permissions": {
            "remove_astp_permissions": remove_astp_perms_schema
        },
        "/assistant/get_group_assistant_conversations": {
            "get_group_assistant_conversations": assistant_id_schema
        },
        "/assistant/get_group_assistant_dashboards": {
            "get_group_assistant_dashboards": get_group_assistant_dashboards_schema
        },
        "/assistant/save_user_rating": {"save_user_rating": save_user_rating_schema},
        "/assistant/get_group_conversations_data": {
            "get_group_conversations_data": get_group_conversations_data_schema
        },
        "/assistant/lookup": {"lookup": lookup_assistant_schema},
        "/assistant/add_path": {"add_assistant_path": add_assistant_path_schema},
        "/assistant/scrape_website": {"scrape_website": scrape_website_schema},
        "/assistant/rescan_websites": {"rescan_websites": rescan_websites_schema},
        "/assistant/extract_sitemap_urls": {"extract_sitemap_urls": extract_sitemap_urls_schema},
        "/assistant/process_drive_sources": {"process_drive_sources": assistant_id_schema},
        "/assistant/register_ops": {"register_ops": tools_op_schema},
    },
    "api_validators": {
        "/assistant/create": {"create": create_assistant_schema},
        "/assistant/delete": {"delete": delete_assistant_schema},
        "/assistant/share": {"share_assistant": share_assistant_schema},
        "/assistant/list": {"list": {}},  # Get
        "/assistant/chat_with_code_interpreter": {"chat": chat_assistant_schema},
        "/assistant/create/codeinterpreter": {
            "create": create_code_interpreter_assistant_schema
        },
        "/assistant/files/download/codeinterpreter": {
            "download": download_ci_files_schema
        },
        "/assistant/openai/thread/delete": {"delete": {}},
        "/assistant/openai/delete": {"delete": {}},
        "/assistant/remove_astp_permissions": {
            "remove_astp_permissions": remove_astp_perms_schema
        },
        "/assistant/get/system_user": {"get": {}},
        "/assistant/get_group_assistant_conversations": {
            "get_group_assistant_conversations": assistant_id_schema
        },
        "/assistant/get_group_assistant_dashboards": {
            "get_group_assistant_dashboards": get_group_assistant_dashboards_schema
        },
        "/assistant/save_user_rating": {"save_user_rating": save_user_rating_schema},
        "/assistant/get_group_conversations_data": {
            "get_group_conversations_data": get_group_conversations_data_schema
        },
        "/assistant/lookup": {"lookup": lookup_assistant_schema},
        "/assistant/add_path": {"add_assistant_path": add_assistant_path_schema},
        "/assistant/request_access": {"share_assistant": assistant_id_schema},
        "/assistant/validate/assistant_id": {"lookup": assistant_id_schema},
        "/assistant/scrape_website": {"scrape_website": scrape_website_schema},
        "/assistant/rescan_websites": {"rescan_websites": rescan_websites_schema},
        "/assistant/extract_sitemap_urls": {"extract_sitemap_urls": extract_sitemap_urls_schema},
        "/assistant/process_drive_sources": {"process_drive_sources": assistant_id_schema},
        "/assistant/register_ops": {"register_ops": tools_op_schema},
    },
}
