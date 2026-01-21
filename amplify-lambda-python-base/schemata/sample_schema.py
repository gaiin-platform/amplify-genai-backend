"""
Every service must define a schema for each operation here. The schema is applied to the data field of the request
body. You do NOT need to include the top-level "data" key in the schema.

Schema Validation:
- Schemas use JSON Schema format
- The @validated decorator automatically validates request data against the schema
- Validation failures return 400 Bad Request with error details

Example request body for this schema:
{
    "data": {
        "msg": "Hello, World!"
    }
}

The "data" wrapper is handled by @validated - you only define the inner structure.
"""
sample_schema = {
    "type": "object",
    "properties": {
        "msg": {
            "type": "string",
            "description": "The message to echo back"
        }
    },
    "required": ["msg"]
}