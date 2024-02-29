import sqlite3
import threading
import pandas as pd
import os
from dotenv import load_dotenv
import logging

load_dotenv(dotenv_path=".env.local")

lock = threading.Lock()  # A lock to guard the shared connection
shared_conn = None  # The shared connection


def init_connection():
    global shared_conn
    if shared_conn is None:
        shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
        directory = os.getenv('LOCAL_DB_DIRECTORY')
        load_db(shared_conn, directory)

    return shared_conn


def get_connection():
    return DatabaseConnection()


class DatabaseConnection:
    def __init__(self):
        self.connection = init_connection()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.connection.close()
        logging.info("Database connection closed.")

    def fetch_schema_info(self):
        with lock:  # Ensure thread-safe access to the database connection
            cursor = self.connection.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            table_names = cursor.fetchall()

            schema_info = {}

            # Fetch columns for each table
            for table_name in table_names:
                cursor.execute(f"PRAGMA table_info({table_name[0]});")
                columns = cursor.fetchall()

                schema_info[table_name[0]] = [
                    {"Field": col[1], "Type": col[2]} for col in columns
                ]

            return schema_info

    # TODO: implement the changes that are in the same function in mysql_db.py
    # These changes are:
    # 1. only sent SELECT queries
    # 2. return data AND columns

    # Should be able to copy the function directly from sql-connector/assistants/db/mysql_db.py
    def execute_query(self, sql_query):
        cursor = self.connection.cursor()
        try:
            print(f"SQL Query Sent To DB:\n{sql_query}")
            # Execute the SQL query and return result
            cursor.execute(sql_query)
            result = cursor.fetchall()

            print(f"Rows returned: {len(result)}")
            logging.info(f"SQL Query Sent To DB:\n{sql_query}")
            cursor.close()
            return result
        except Exception as e:
            cursor.close()
            logging.error(f"Error executing query:\n{sql_query}\nException: {e}")
            raise


def load_db(conn, directory):
    """
    Load all CSV files in a directory into an in-memory SQLite database.
    """
    csv_files = [file for file in os.listdir(directory) if file.endswith('.csv')]

    with lock:  # Use the lock to prevent concurrent access to the connection

        for file in csv_files:
            table_name = os.path.splitext(file)[0]
            file_path = os.path.join(directory, file)
            df = pd.read_csv(file_path)
            df.to_sql(table_name, conn, if_exists='replace', index=False)

        conn.commit()


__all__ = ["get_connection"]
