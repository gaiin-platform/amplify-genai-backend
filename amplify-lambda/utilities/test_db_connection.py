import json
import os
import logging
from common.validate import validated

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


@validated("test_connection")
def lambda_handler(event, context, current_user=None, name=None, data=None):
    try:
        logger.info("Received event for database connection test")
        body = event.get("body")
        if isinstance(body, str):
            body = json.loads(body)

        # Handle nested data structure
        if isinstance(body, dict) and "data" in body:
            db_config = body["data"].get("config", {})
            db_type = body["data"].get("type")
        else:
            db_config = body if isinstance(body, dict) else {}
            db_type = db_config.get("type")

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
