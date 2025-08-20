"""
Auto-registration module for Lambda operations.
Handles discovery and registration of @api_tool decorated functions within Lambda environments.
"""
from pycommon.api.tools_ops import api_tools_register_handler
from pycommon.authz import setup_validated, validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

@validated(op="register_ops")
def api_tools_handler(event, context, current_user, name, data):
    data = data["data"]
    command = data.get("command", "ls")
    
    # Configure directories to include (more precise than excluding)
    include_dirs = []  # Main code directories including embedding folder
    
    result = api_tools_register_handler(include_dirs, command, data)
    result["service"] = "embedding"
    return result
