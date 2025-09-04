import json
import os
import uuid
import boto3
from datetime import datetime
from botocore.exceptions import ClientError
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DB_CONNECTIONS_TABLE"])

# Database type specific required fields
DB_TYPE_REQUIRED_FIELDS = {
    "postgres": ["host", "port", "database", "username", "password"],
    "mysql": ["host", "port", "database", "username", "password"],
    "mssql": ["host", "port", "database", "username", "password"],
    "duckdb": ["database"],
    "sqlite": ["database"],
    "snowflake": ["account", "warehouse", "database", "schema", "username", "password"],
    "bigquery": [
        "project_id",
        "credentials",
    ],  # BigQuery uses service account credentials
    "oracle": ["host", "port", "service_name", "username", "password"],
}


@validated("save_connection")
def lambda_handler(event, context, current_user, name, data):
    try:
        # Debug logging
        # print("=== Debug Info ===")
        # print("Event:", json.dumps(event, indent=2))
        # print("Data:", json.dumps(data, indent=2))
        # print("Current User:", current_user)
        # print("Name:", name)
        # print("=================")

        # Get the request body from the nested data field
        body = data.get("data", {})
        if not body:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {"success": False, "message": "Missing data field in request body"}
                ),
            }

        # Extract common fields
        db_type = body.get("type", "postgres")  # Default to postgres if not specified
        # Get connection name from 'name' field
        connection_name = (
            body.get("name")
            or f"{db_type} Connection {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Debug logging for extracted fields
        # print("=== Extracted Fields ===")
        # print("Connection Name:", connection_name)
        # print("DB Type:", db_type)
        # print("======================")

        # Validate database type specific fields
        required_fields = DB_TYPE_REQUIRED_FIELDS.get(db_type)
        if not required_fields:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": f"Unsupported database type: {db_type}",
                    }
                ),
            }

        # Check if all required fields for the specific database type are present
        missing_fields = [field for field in required_fields if not body.get(field)]
        if missing_fields:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": f"Missing required fields for {db_type}: {', '.join(missing_fields)}",
                    }
                ),
            }

        # Create a unique ID for the connection
        connection_id = str(uuid.uuid4())

        # Create the item to save with all provided fields
        item = {
            "id": connection_id,
            "user": current_user,  # Use the validated current_user
            "connection_name": connection_name,  # Store as connection_name in DB
            "type": db_type,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

        # Add all provided fields from the request body
        for key, value in body.items():
            if key not in [
                "name",  # Skip name as we've already handled it
                "type",
            ]:  # Skip common fields already added
                item[key] = value

        # Debug logging for final item
        # print("=== Final Item ===")
        # print(json.dumps(item, indent=2))
        # print("================")

        # Save to DynamoDB
        try:
            table.put_item(Item=item)
        except ClientError as e:
            print(f"DynamoDB error: {str(e)}")  # Add logging
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"success": False, "message": f"Database error: {str(e)}"}
                ),
            }

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "success": True,
                    "message": "Database connection saved successfully",
                    "data": {
                        "connection_id": connection_id,
                        "name": connection_name,  # Return as 'name' to match frontend interface
                    },
                }
            ),
        }

    except Exception as e:
        print(f"Unexpected error: {str(e)}")  # Add logging
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"success": False, "message": f"Unexpected error: {str(e)}"}
            ),
        }
