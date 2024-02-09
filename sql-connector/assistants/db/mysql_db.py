import logging
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
import os

# TODO check if .env exists so that this doesn't have to be removed/added for local testing
# load_dotenv()  # Load environment variables from .env file
# Set the values below in a local .env file in the root of the project for local testing
load_dotenv(".env.local")
mysql_host = os.environ["MYSQL_DB_HOST"]
mysql_user = os.environ["MYSQL_DB_USERNAME"]
mysql_database = os.environ["MYSQL_DB_NAME"]
mysql_password = os.environ["MYSQL_DB_PASSWORD"]


# The DatabaseConnection class is a context manager for handling MySQL database connections.
# It simplifies the process of connecting to and disconnecting from the database, ensuring that
# connections are properly established and closed. This class uses the 'with' statement context
# manager protocol to automatically manage resources. When entering the context (using 'with'),
# it establishes a connection to the MySQL database using the provided credentials. Upon exiting
# the context, it ensures that the connection is closed, even if an error occurs during
# database operations. This approach prevents connection leaks and makes the code cleaner
# and more maintainable by abstracting the connection logic.
class DatabaseConnection:
    def __init__(self, host, database, user, password, port=3306):
        self.host = host
        self.database = database
        self.user = user
        self.password = password
        self.port = port
        self.connection = None

    def __enter__(self):
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                database=self.database,
                user=self.user,
                password=self.password,
                port=self.port,
            )
            logging.info("Database connection established.")
            return self
        except Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logging.info("Database connection closed.")

    def fetch_schema_info(self):
        schema_info = {}
        try:
            with self.connection.cursor() as cursor:
                # Fetch all table names
                cursor.execute("SHOW TABLES;")
                tables = [table[0] for table in cursor.fetchall()]

                # Fetch columns for each table
                for table in tables:
                    cursor.execute(f"DESCRIBE {table};")
                    columns = cursor.fetchall()
                    schema_info[table] = [
                        {"Field": col[0], "Type": col[1]} for col in columns
                    ]

            logging.info("Schema information fetched.")
        except Exception as e:
            logging.error(f"Error fetching schema: {e}")
            raise

        return schema_info

    # this function only allows SELECT queries, no other types
    def execute_query(self, sql_query):
        if not sql_query.strip().lower().startswith("select"):
            raise ValueError("Only SELECT queries are allowed.")
        try:
            with self.connection.cursor(dictionary=True) as cursor:
                cursor.execute(sql_query)
                result = cursor.fetchall()
                # Assuming 'cursor.description' holds column headers
                columns = [desc[0] for desc in cursor.description]
                return {"columns": columns, "rows": result}
        except Exception as e:
            logging.error(f"Error executing query: {sql_query}, Error: {e}")
            raise

def get_connection():
    return DatabaseConnection(
        mysql_host, mysql_database, mysql_user, mysql_password
    )



__all__ = ["get_connection"]