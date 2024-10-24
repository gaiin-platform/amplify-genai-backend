import csv
import sqlite3
import threading
from io import StringIO
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

lock = threading.Lock()  # A lock to guard the shared connection
shared_conn = None  # The shared connection

def initialize_connection():
    global shared_conn
    shared_conn = sqlite3.connect(":memory:", check_same_thread=False)

def get_connection():
    """
    Function to get a shared connection.
    """
    global shared_conn
    if shared_conn is None:
        raise Exception("The shared connection was not initialized.")
    return shared_conn

def load():
    directory = os.getenv('LOCAL_DB_DIRECTORY')
    return load_db(directory)

def load_db(directory):
    """
    Load all CSV files in a directory into an in-memory SQLite database.
    """
    csv_files = [file for file in os.listdir(directory) if file.endswith('.csv')]

    with lock:  # Use the lock to prevent concurrent access to the connection
        conn = get_connection()

        for file in csv_files:
            table_name = os.path.splitext(file)[0]
            file_path = os.path.join(directory, file)
            df = pd.read_csv(file_path)
            df.to_sql(table_name, conn, if_exists='replace', index=False)

        conn.commit()

def describe_database_schema():
    with lock:  # Ensure thread-safe access to the database connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        table_names = cursor.fetchall()

        schema_string = ""

        for table_name in table_names:
            # Schema Info
            cursor.execute(f"PRAGMA table_info({table_name[0]});")
            columns = cursor.fetchall()

            schema_string += f"\nTable: {table_name[0]}\n---------------\n"
            schema_string += "Column\t\tType\n"

            for column in columns:
                schema_string += f"{column[1]}\t\t{column[2]}\n"

            # Sample Data
            schema_string += "\n\nSample Rows:\n---------------\n"
            cursor.execute(f"SELECT * FROM {table_name[0]} LIMIT 10;")  # Get 10 sample rows
            sample_rows = cursor.fetchall()
            for row in sample_rows:
                row_data = "\t".join(str(item) for item in row)
                schema_string += f"{row_data}\n"

            schema_string += "\n"  # Separator between tables

        return schema_string

def execute_query_and_get_result_str(query):
    try:
        with lock:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(query)

            rows = cursor.fetchall()

            column_names = [description[0] for description in cursor.description]

            csv_buffer = StringIO()

            csv_writer = csv.writer(csv_buffer)

            if len(rows) > 0:
                csv_writer.writerow(column_names)

                csv_writer.writerows(rows)

                csv_data = csv_buffer.getvalue()

                csv_buffer.close()

                return csv_data.strip()
            else:
                return "The query didn't return any results."
    except Exception as e:
        return str(e)