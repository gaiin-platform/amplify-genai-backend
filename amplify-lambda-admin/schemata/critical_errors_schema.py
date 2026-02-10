"""
Schema validation rules for critical error tracking endpoints.

These schemas define the expected structure of request data for:
- GET /amplifymin/critical_errors (get_critical_errors_admin)
- POST /amplifymin/critical_errors/resolve (resolve_critical_error_admin)
"""

# Schema for GET /amplifymin/critical_errors
# Note: This endpoint only returns ACTIVE (unresolved) errors
# Resolved errors are not shown on the frontend
get_critical_errors_schema = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Maximum number of errors to return"
        },
        "last_evaluated_key": {
            "type": "object",
            "description": "Pagination token from previous request",
            "properties": {
                "error_id": {"type": "string"},
                "status": {"type": "string"},
                "timestamp": {"type": "number"}
            }
        }
    },
    "additionalProperties": False
}


# Schema for POST /amplifymin/critical_errors/resolve
resolve_critical_error_schema = {
    "type": "object",
    "properties": {
        "error_id": {
            "type": "string",
            "minLength": 36,
            "maxLength": 36,
            "description": "UUID of the error to resolve"
        },
        "resolution_notes": {
            "type": "string",
            "maxLength": 5000,
            "description": "Optional notes about the resolution"
        }
    },
    "required": ["error_id"],
    "additionalProperties": False
}
