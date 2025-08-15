import json
import os
import logging
import boto3
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DB_CONNECTIONS_TABLE"])


def get_raw_connection_data(connection_id, user):
    """Fetch raw connection data directly from DynamoDB."""
    try:
        logger.info(
            f"Fetching raw connection data for ID: {connection_id}, User: {user}"
        )

        # Query the table for the specific connection using only id as primary key
        response = table.get_item(Key={"id": connection_id})

        if "Item" not in response:
            logger.error(f"Connection not found: {connection_id}")
            return None

        item = response["Item"]

        # Verify that the connection belongs to the current user
        if item.get("user") != user:
            logger.error(f"Connection {connection_id} does not belong to user {user}")
            return None

        logger.info(
            f"Raw connection data retrieved. Password length: {len(item.get('password', ''))}"
        )

        return item

    except Exception as e:
        logger.error(f"Error fetching raw connection data: {str(e)}")
        return None


@validated("test_connection")
def lambda_handler(event, context, current_user=None, name=None, data=None):
    try:
        logger.info("Received event for database connection test")
        logger.info(f"Current user: {current_user}")
        logger.info(f"Event body: {json.dumps(event.get('body', {}), indent=2)}")

        body = event.get("body")
        if isinstance(body, str):
            body = json.loads(body)

        # Handle nested data structure
        if isinstance(body, dict) and "data" in body:
            request_data = body["data"]
            connection_id = request_data.get("connection_id")
            db_config = request_data.get("config", {})
            db_type = request_data.get("type")
        else:
            request_data = body if isinstance(body, dict) else {}
            connection_id = request_data.get("connection_id")
            db_config = request_data.get("config", {})
            db_type = request_data.get("type")

        logger.info(f"Connection ID: {connection_id}")
        logger.info(f"DB Type: {db_type}")
        logger.info(f"Config keys: {list(db_config.keys()) if db_config else 'None'}")

        # If we have a connection_id, fetch the raw data from DynamoDB
        if connection_id:
            logger.info(f"Fetching raw connection data for ID: {connection_id}")
            raw_data = get_raw_connection_data(connection_id, current_user)
            if raw_data:
                db_config = raw_data
                db_type = raw_data.get("type")
                logger.info(f"Using raw data from DynamoDB. Type: {db_type}")
            else:
                return {
                    "statusCode": 404,
                    "body": json.dumps(
                        {
                            "success": False,
                            "error": f"Connection not found: {connection_id}",
                        }
                    ),
                }
        else:
            logger.info("No connection_id provided, using config from request")
            # Check if the password looks like it might be masked
            password = db_config.get("password")
            if password and password == "********":
                logger.warning(
                    "Password appears to be masked (********). This suggests the frontend is passing masked data from get_db_connections."
                )
                logger.warning(
                    "To fix this, the frontend should pass connection_id instead of the masked config."
                )
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {
                            "success": False,
                            "error": "Password appears to be masked. Please pass connection_id instead of masked config data.",
                        }
                    ),
                }

        # Log the config structure (without sensitive data)
        safe_config = {
            k: v for k, v in db_config.items() if k not in ["password", "credentials"]
        }
        logger.info(f"Database config structure: {json.dumps(safe_config, indent=2)}")

        # Check if password is present
        password = db_config.get("password")
        if password:
            logger.info(
                f"Password found in config. Length: {len(password)}, Preview: {password[:8]}..."
            )
        else:
            logger.info("No password found in config")

        logger.info(f"Testing connection for database type: {db_type}")
        result = test_db_connection(db_type, db_config)
        logger.info(f"Connection test result: {result}")
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": str(e)}),
        }


def test_db_connection(db_type, config):
    try:
        logger.info(f"Starting database connection test for type: {db_type}")
        logger.info(
            f"Connection config: {json.dumps({k: v for k, v in config.items() if k != 'password'})}"
        )

        # Log password info right before connection attempt
        password = config.get("password")
        if password:
            logger.info(
                f"Using password for connection - Length: {len(password)}, Preview: {password[:8]}..."
            )
        else:
            logger.warning("No password found in config for connection")

        if db_type == "postgres":
            logger.info("Attempting PostgreSQL connection")
            import psycopg2

            conn = psycopg2.connect(
                host=config.get("host"),
                port=config.get("port", 5432),
                dbname=config.get("database"),
                user=config.get("username"),
                password=config.get("password"),
                connect_timeout=5,
            )
            logger.info("PostgreSQL connection successful")
            conn.close()
            logger.info("PostgreSQL connection closed")
        elif db_type == "mysql":
            logger.info("Attempting MySQL connection")
            import pymysql

            conn = pymysql.connect(
                host=config.get("host"),
                port=int(config.get("port", 3306)),
                db=config.get("database"),
                user=config.get("username"),
                password=config.get("password"),
                connect_timeout=5,
            )
            logger.info("MySQL connection successful")
            conn.close()
            logger.info("MySQL connection closed")
        elif db_type == "mssql":
            logger.info("Attempting MSSQL connection")
            import pyodbc

            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={config.get('host')},{config.get('port', 1433)};"
                f"DATABASE={config.get('database')};UID={config.get('username')};PWD={config.get('password')}"
            )
            logger.info(
                f"MSSQL connection string (without password): {conn_str.replace(config.get('password', ''), '*****')}"
            )
            conn = pyodbc.connect(conn_str, timeout=5)
            logger.info("MSSQL connection successful")
            conn.close()
            logger.info("MSSQL connection closed")
        elif db_type == "duckdb":
            logger.info("Attempting DuckDB connection")
            import duckdb

            conn = duckdb.connect(database=config.get("database", ":memory:"))
            logger.info("DuckDB connection successful")
            conn.close()
            logger.info("DuckDB connection closed")
        elif db_type == "sqlite":
            logger.info("Attempting SQLite connection")
            import sqlite3

            conn = sqlite3.connect(config.get("database", ":memory:"))
            logger.info("SQLite connection successful")
            conn.close()
            logger.info("SQLite connection closed")
        elif db_type == "snowflake":
            logger.info("Attempting Snowflake connection")
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
            logger.info("Snowflake connection successful")
            conn.close()
            logger.info("Snowflake connection closed")
        elif db_type == "bigquery":
            logger.info("Attempting BigQuery connection")
            from google.cloud import bigquery

            client = bigquery.Client()
            logger.info("BigQuery client created")
            # Try listing datasets as a test
            datasets = list(client.list_datasets())
            logger.info(f"Successfully listed {len(datasets)} BigQuery datasets")
        elif db_type == "oracle":
            logger.info("Attempting Oracle connection")
            import cx_Oracle

            dsn = cx_Oracle.makedsn(
                config.get("host"),
                int(config.get("port", 1521)),
                service_name=config.get("database"),
            )
            logger.info(f"Oracle DSN created: {dsn}")
            conn = cx_Oracle.connect(
                user=config.get("username"),
                password=config.get("password"),
                dsn=dsn,
                encoding="UTF-8",
                timeout=5,
            )
            logger.info("Oracle connection successful")
            conn.close()
            logger.info("Oracle connection closed")
        else:
            logger.error(f"Unsupported database type: {db_type}")
            return {"success": False, "error": f"Unsupported db type: {db_type}"}

        logger.info("Database connection test completed successfully")
        return {"success": True}
    except Exception as e:
        logger.error(f"Error in test_db_connection: {str(e)}", exc_info=True)
        return {"success": False, "error": str(e)}
