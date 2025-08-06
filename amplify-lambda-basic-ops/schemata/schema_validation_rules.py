from .query_schema import query_schema
from .workflow_schema import workflow_schema
from .user_data_put_schema import user_data_put_schema
from .user_data_get_schema import user_data_get_schema
from .user_data_uuid_get_schema import user_data_uuid_get_schema
from .user_data_query_range_schema import user_data_query_range_schema
from .user_data_query_prefix_schema import user_data_query_prefix_schema
from .user_data_query_type_schema import user_data_query_type_schema
from .user_data_delete_schema import user_data_delete_schema
from .user_data_batch_put_schema import user_data_batch_put_schema
from .user_data_batch_get_schema import user_data_batch_get_schema
from .user_data_batch_delete_schema import user_data_batch_delete_schema
from .user_data_uuid_delete_schema import user_data_uuid_delete_schema

validators = {
    "/llm/query": {"query": query_schema},
    "/llm/qa_check": {"qa_check": {}},
    "/llm/workflow": {"workflow": workflow_schema},
    "/llm/workflow-start": {"workflow": workflow_schema},
    "/work/echo": {"echo": {}},
    "/work/session/create": {"create": {}},
    "/work/session/add_record": {"add": {}},
    "/work/session/list_records": {"list": {}},
    "/work/session/delete_record": {"delete": {}},
    "/work/session/stitch_records": {"stitch": {}},
    "/user-data/put": user_data_put_schema,
    "/user-data/get": user_data_get_schema,
    "/user-data/get-by-uuid": user_data_uuid_get_schema,
    "/user-data/query-range": user_data_query_range_schema,
    "/user-data/query-prefix": user_data_query_prefix_schema,
    "/user-data/query-type": user_data_query_type_schema,
    "/user-data/delete": user_data_delete_schema,
    "/user-data/batch-put": user_data_batch_put_schema,
    "/user-data/batch-get": user_data_batch_get_schema,
    "/user-data/batch-delete": user_data_batch_delete_schema,
    "/user-data/delete-by-uuid": user_data_uuid_delete_schema,
}


rules = {"validators": validators, "api_validators": validators}
