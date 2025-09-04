from .get_ops_schema import get_ops_schema
from .register_ops_schema import register_ops_schema
from .delete_op_schema import delete_op_schema
from .get_op_schema import get_op_schema
from .tools_op_schema import tools_op_schema

rules = {
    "validators": {
        "/ops/get": {"get": get_ops_schema},
        "/ops/get_all": {"get": {}},
        "/ops/register": {"write": register_ops_schema},
        "/ops/delete": {"delete": delete_op_schema},
        "/ops/get_op": {"get": get_op_schema},
        "/ops/register_ops": {"register_ops": tools_op_schema}, 
    },
    "api_validators": {
        "/ops/get": {"get": get_ops_schema},
        "/ops/register": {"write": register_ops_schema},
        "/ops/get_op": {"get": get_op_schema},
        "/ops/register_ops": {"register_ops": tools_op_schema},
    },
}
