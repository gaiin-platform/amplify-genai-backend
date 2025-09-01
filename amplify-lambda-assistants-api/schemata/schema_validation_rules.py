from .execute_custom_auto_schema import execute_custom_auto_schema
from .oauth_user_delete_schema import oauth_user_delete_schema
from .job_get_result_schema import job_get_result_schema
from .job_set_result_schema import job_set_result_schema
from .oauth_user_get_schema import oauth_user_get_schema
from .integration_user_files_schema import integration_user_files_schema
from .integration_user_files_download_schema import integration_user_files_download_schema
from .integration_drive_files_ds_schema import integration_drive_files_ds_schema
from .oauth_register_secret_schema import oauth_register_secret_schema
from .oauth_user_refresh_token_schema import oauth_user_refresh_token_schema

rules = {
    "validators": {
        "/assistant-api/execute-custom-auto": {
            "execute_custom_auto": execute_custom_auto_schema
        },
        "/integrations/oauth/start-auth": {"start_oauth": {}},
        "/integrations/oauth/user/delete": oauth_user_delete_schema,
        "/assistant-api/get-job-result": job_get_result_schema,
        "/assistant-api/set-job-result": job_set_result_schema,
        "/integrations/list_supported": {"list_integrations": {}},
        "/integrations/oauth/user/list": {"list_integrations": {}},
        "/integrations/oauth/user/get": oauth_user_get_schema,
        "/integrations/user/files": integration_user_files_schema,
        "/integrations/user/files/download": integration_user_files_download_schema,
        "/integrations/user/files/upload": integration_drive_files_ds_schema,
        "/integrations/oauth/register_secret": oauth_register_secret_schema,
        "/integrations/oauth/refresh_token": oauth_user_refresh_token_schema,
    },
    "api_validators": {
        "/assistant-api/execute-custom-auto": {
            "execute_custom_auto": execute_custom_auto_schema
        },
        "/integrations/oauth/user/delete": oauth_user_delete_schema,
        "/assistant-api/get-job-result": job_get_result_schema,
        "/assistant-api/set-job-result": job_set_result_schema,
        "/integrations/oauth/user/list": {"list_integrations": {}},
        "/integrations/oauth/user/get": oauth_user_get_schema,
        "/integrations/user/files": integration_user_files_schema,
        "/integrations/user/files/download": integration_user_files_download_schema,
        "/integrations/user/files/upload": integration_drive_files_ds_schema,
        "/integrations/oauth/refresh_token": oauth_user_refresh_token_schema,
    }
}
