import json
import os
import uuid
import boto3
import logging
from datetime import datetime
from botocore.exceptions import ClientError
from typing import Dict, Any
from pycommon.api.ops import api_tool

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB
dynamodb = boto3.resource("dynamodb") if os.environ.get("AWS_REGION") else None
table = dynamodb.Table(os.environ["DB_CONNECTIONS_TABLE"]) if dynamodb and os.environ.get("DB_CONNECTIONS_TABLE") else None

# Database type specific required fields
DB_TYPE_REQUIRED_FIELDS = {
    "postgres": ["host", "port", "database", "username", "password"],
    "mysql": ["host", "port", "database", "username", "password"], 
    "mssql": ["host", "port", "database", "username", "password"],
    "sqlite": ["database"],
    "snowflake": ["account", "warehouse", "database", "schema", "username", "password"],
    "oracle": ["host", "port", "service_name", "username", "password"],
}


@api_tool(
    path="/vu-agent/db/get-connections",
    tags=["database", "default"],
    name="getDbConnections",
    description="Get database connections for the current user.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "connection_name": {"type": "string"},
                        "type": {"type": "string"},
                        "host": {"type": "string"},
                        "port": {"type": "number"},
                        "database": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "created_at": {"type": "string"},
                        "updated_at": {"type": "string"},
                    },
                },
            },
            "total_connections": {"type": "number"},
        },
        "required": ["connections", "total_connections"],
    },
)
def get_db_connections_handler(current_user, access_token):
    """Get database connections for the current user with masked sensitive fields."""
    try:
        logger.info(f"Getting database connections for user: {current_user}")
        
        # Check if table is properly initialized
        if not table:
            logger.error("DB_CONNECTIONS_TABLE environment variable not set or DynamoDB table not accessible")
            return {
                "connections": [],
                "total_connections": 0,
            }
        
        # Query the table using the UserIndex GSI
        response = table.query(
            IndexName="UserIndex",
            KeyConditionExpression="#user = :user",
            ExpressionAttributeNames={"#user": "user"},
            ExpressionAttributeValues={":user": current_user},
        )
        
        items = response.get("Items", [])
        logger.info(f"Found {len(items)} database connections for user")
        
        # Mask sensitive fields in the response for security
        def mask_sensitive_fields(item):
            """Mask sensitive fields in database connection items."""
            masked_item = item.copy()
            sensitive_fields = ["password", "credentials"]
            
            for field in sensitive_fields:
                if field in masked_item and masked_item[field]:
                    # Replace with asterisks to indicate the field exists but hide the value
                    masked_item[field] = "********"
            
            return masked_item
        
        # Apply masking to all items
        masked_items = [mask_sensitive_fields(item) for item in items]
        
        return {
            "connections": masked_items,
            "total_connections": len(items),
        }
        
    except Exception as e:
        logger.error(f"Error getting database connections: {str(e)}")
        raise RuntimeError(f"Failed to get database connections: {str(e)}")


@api_tool(
    path="/vu-agent/db/save-connection",
    tags=["database", "default"],
    name="saveDbConnection",
    description="Save a new database connection.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Connection name"},
            "type": {"type": "string", "description": "Database type"},
            "host": {"type": "string", "description": "Database host"},
            "port": {"type": "number", "description": "Database port"},
            "database": {"type": "string", "description": "Database name"},
            "username": {"type": "string", "description": "Database username"},
            "password": {"type": "string", "description": "Database password"},
            "account": {"type": "string", "description": "Account (for Snowflake)"},
            "warehouse": {"type": "string", "description": "Warehouse (for Snowflake)"},
            "schema": {"type": "string", "description": "Schema name"},
            "service_name": {"type": "string", "description": "Service name (for Oracle)"},
        },
        "required": ["type"],
    },
    output={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string"},
            "name": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["connection_id", "name", "message"],
    },
)
def save_db_connection_handler(current_user, access_token, **connection_data):
    """Save a new database connection for the current user."""
    try:
        logger.info(f"Saving database connection for user: {current_user}")
        
        # Extract common fields
        db_type = connection_data.get("type", "postgres")
        connection_name = (
            connection_data.get("name")
            or f"{db_type} Connection {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Validate database type specific fields
        required_fields = DB_TYPE_REQUIRED_FIELDS.get(db_type)
        if not required_fields:
            raise ValueError(f"Unsupported database type: {db_type}")
        
        # Check if all required fields for the specific database type are present
        missing_fields = [field for field in required_fields if not connection_data.get(field)]
        if missing_fields:
            raise ValueError(f"Missing required fields for {db_type}: {', '.join(missing_fields)}")
        
        # Create a unique ID for the connection
        connection_id = str(uuid.uuid4())
        
        # Create the item to save with all provided fields
        item = {
            "id": connection_id,
            "user": current_user,
            "connection_name": connection_name,
            "type": db_type,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # Add all provided fields from the request
        for key, value in connection_data.items():
            if key not in ["name", "type"] and value is not None:
                item[key] = value
        
        # Check if table is properly initialized
        if not table:
            logger.error("DB_CONNECTIONS_TABLE environment variable not set or DynamoDB table not accessible")
            raise RuntimeError("Database connections table not accessible")
        
        # Save to DynamoDB
        table.put_item(Item=item)
        logger.info(f"Saved database connection: {connection_id}")
        
        return {
            "connection_id": connection_id,
            "name": connection_name,
            "message": "Database connection saved successfully",
        }
        
    except Exception as e:
        logger.error(f"Error saving database connection: {str(e)}")
        raise RuntimeError(f"Failed to save database connection: {str(e)}")


@api_tool(
    path="/vu-agent/db/test-connection",
    tags=["database", "default"],
    name="testDbConnection",
    description="Test a database connection.",
    parameters={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "description": "Connection ID to test"},
            "type": {"type": "string", "description": "Database type"},
            "config": {"type": "object", "description": "Connection configuration"},
        },
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "error": {"type": "string"},
        },
        "required": ["success"],
    },
)
def test_db_connection_handler(current_user, access_token, connection_id=None, type=None, config=None):
    """Test a database connection by ID or configuration."""
    try:
        logger.info(f"Testing database connection for user: {current_user}")
        
        # Check if table is properly initialized
        if not table:
            logger.error("DB_CONNECTIONS_TABLE environment variable not set or DynamoDB table not accessible")
            return {"success": False, "error": "Database connections table not accessible"}
        
        # If we have a connection_id, fetch the raw data from DynamoDB
        if connection_id:
            logger.info(f"Fetching connection data for ID: {connection_id}")
            response = table.get_item(Key={"id": connection_id})
            
            if "Item" not in response:
                raise ValueError(f"Connection not found: {connection_id}")
            
            item = response["Item"]
            
            # Verify that the connection belongs to the current user
            if item.get("user") != current_user:
                raise ValueError(f"Connection {connection_id} does not belong to user {current_user}")
            
            db_config = item
            db_type = item.get("type")
        else:
            # Use provided config and type
            db_config = config or {}
            db_type = type
            
            # Check if the password looks like it might be masked
            password = db_config.get("password")
            if password and password == "********":
                raise ValueError("Password appears to be masked. Please pass connection_id instead of masked config data.")
        
        if not db_type:
            raise ValueError("Database type is required")
        
        logger.info(f"Testing connection for database type: {db_type}")
        result = test_db_connection(db_type, db_config)
        logger.info(f"Connection test result: {result}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error testing database connection: {str(e)}")
        return {"success": False, "error": str(e)}


def test_db_connection(db_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Test database connection based on type and configuration."""
    try:
        logger.info(f"Starting database connection test for type: {db_type}")
        
        if db_type == "postgres":
            import psycopg2
            conn = psycopg2.connect(
                host=config.get("host"),
                port=config.get("port", 5432),
                dbname=config.get("database"),
                user=config.get("username"),
                password=config.get("password"),
                connect_timeout=5,
            )
            conn.close()
            
        elif db_type == "mysql":
            import pymysql
            conn = pymysql.connect(
                host=config.get("host"),
                port=int(config.get("port", 3306)),
                db=config.get("database"),
                user=config.get("username"),
                password=config.get("password"),
                connect_timeout=5,
            )
            conn.close()
            
        elif db_type == "mssql":
            import pyodbc
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={config.get('host')},{config.get('port', 1433)};"
                f"DATABASE={config.get('database')};UID={config.get('username')};PWD={config.get('password')}"
            )
            conn = pyodbc.connect(conn_str, timeout=5)
            conn.close()
            
        elif db_type == "sqlite":
            import sqlite3
            conn = sqlite3.connect(config.get("database", ":memory:"))
            conn.close()
            
        elif db_type == "snowflake":
            import snowflake.connector
            conn = snowflake.connector.connect(
                user=config.get("username"),
                password=config.get("password"),
                account=config.get("account"),
                warehouse=config.get("warehouse"),
                database=config.get("database"),
                schema=config.get("schema"),
                login_timeout=5,
            )
            conn.close()
            
        elif db_type == "oracle":
            import cx_Oracle
            dsn = cx_Oracle.makedsn(
                config.get("host"),
                int(config.get("port", 1521)),
                service_name=config.get("service_name"),
            )
            conn = cx_Oracle.connect(
                user=config.get("username"),
                password=config.get("password"),
                dsn=dsn,
                encoding="UTF-8",
                timeout=5,
            )
            conn.close()
            
        else:
            return {"success": False, "error": f"Unsupported database type: {db_type}"}
        
        logger.info("Database connection test completed successfully")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error in test_db_connection: {str(e)}")
        return {"success": False, "error": str(e)}


@api_tool(
    path="/vu-agent/db/delete-connection",
    tags=["database", "default"],
    name="deleteDbConnection",
    description="Delete a database connection.",
    parameters={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "description": "Connection ID to delete"},
        },
        "required": ["connection_id"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "error": {"type": "string"},
        },
        "required": ["success"],
    },
)
def delete_db_connection_handler(current_user, access_token, connection_id):
    """Delete a database connection by ID."""
    try:
        logger.info(f"Deleting database connection {connection_id} for user: {current_user}")
        
        # Check if table is properly initialized
        if not table:
            logger.error("DB_CONNECTIONS_TABLE environment variable not set or DynamoDB table not accessible")
            return {"success": False, "error": "Database connections table not accessible"}
        
        # First, verify the connection exists and belongs to the user
        response = table.get_item(Key={"id": connection_id})
        
        if "Item" not in response:
            logger.warning(f"Connection not found: {connection_id}")
            return {"success": False, "error": f"Connection not found: {connection_id}"}
        
        item = response["Item"]
        
        # Verify that the connection belongs to the current user
        if item.get("user") != current_user:
            logger.warning(f"Connection {connection_id} does not belong to user {current_user}")
            return {"success": False, "error": f"Connection {connection_id} does not belong to user {current_user}"}
        
        # Delete the connection
        table.delete_item(Key={"id": connection_id})
        logger.info(f"Successfully deleted database connection: {connection_id}")
        
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error deleting database connection: {str(e)}")
        return {"success": False, "error": str(e)}