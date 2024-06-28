import os
import uuid

import sqlalchemy
from sqlalchemy import text, inspect

from db.registry import load_db_by_id
from llm.chat import chat


def llm_chat_query_db(current_user, access_token, account, db_id, db_schema, query, model):
    """
    Query the database using the LLM.

    Args:
        accessToken (str): The access token for the user.
        account (str): The account name.
        model (str): The model name.
        query (str): The query to execute.

    Returns:
        list: A list of dictionaries representing the query results.
        :param model:
        :param db_id:
        :param query:
        :param access_token:
        :param account:
        :param current_user:
    """

    print(f"Querying database with ID {db_id} using LLM for user {current_user} with Model {model} and query: {query}")

    payload = {
        "model": model,
        "temperature": 1,
        "max_tokens": 1000,
        "stream": True,
        "dataSources": [],
        "messages": [
            {
                "role": "user",
                "content":
f"""
The database schema is:
----------
{db_schema}
----------

Please create a SQL query for the task:                
----------
{query}
----------

In the format:
thought: "<Insert your step-by-step thoughts in one sentence>"
sql: "<Insert SQL Query Here with no line breaks>"
""",
                "type": "prompt",
                "data": {},
                "id": "example-id-1234"
            }
        ],
        "options": {
            "requestId": str(uuid.uuid4()),
            "model": {
                "id": model,
            },
            "prompt": "Follow the user's instructions carefully. Respond using the exact format specified.",
            "ragOnly": True,
        }
    }

    chat_endpoint = os.getenv('CHAT_ENDPOINT')
    if not chat_endpoint:
        raise ValueError("Environment variable 'CHAT_ENDPOINT' is not set.")

    response, _ = chat(chat_endpoint, access_token, payload)

    print(f"LLM Response: {response}")

    # Query the database
    return response


def query_db(conn_info, sql_query):
    conn, engine_type = conn_info
    engine = sqlalchemy.create_engine(engine_type, creator=lambda: conn)
    with engine.connect() as connection:
        result = connection.execute(text(sql_query))
        # Step 4: Fetch results as dictionary
        result_set = [dict(row) for row in result.mappings()]

    return result_set


def describe_schemas_from_user_db(current_user, db_id):
    conn_info = load_db_by_id(current_user, db_id)
    return describe_schemas_from_db(conn_info)


def describe_schemas_from_db(conn_info):

    conn, engine_type = conn_info

    print(f"Describing schema for database with engine type: {engine_type}")

    # Create an engine and inspector for schema inspection
    engine = sqlalchemy.create_engine(engine_type, creator=lambda: conn)
    inspector = inspect(engine)

    schema_descriptions = []

    # Fetch all table names
    tables = inspector.get_table_names()
    for table in tables:
        columns_info = inspector.get_columns(table)
        table_description = {
            "table": table,
            "columns": []
        }
        for column in columns_info:
            column_description = {
                "name": column['name'],
                "type": str(column['type']),
                "nullable": column['nullable']
            }
            table_description["columns"].append(column_description)
        schema_descriptions.append(table_description)

    return schema_descriptions



def convert_schema_dicts_to_text(schema_dicts):
    schema_texts = []

    for table in schema_dicts:
        table_text = f"Table: {table['table']}\n"
        for column in table['columns']:
            column_text = f"  Column: {column['name']}, Type: {column['type']}, Nullable: {column['nullable']}\n"
            table_text += column_text
        schema_texts.append(table_text)

    return "\n".join(schema_texts)


def query_db_by_id(current_user, db_id, sql_query):
    """
    Query the SQLite database with the given ID using the specified SQL query.

    Args:
        db_id (str): The unique identifier of the database.
        sql_query (str): The SQL query to execute.

    Returns:
        list: A list of dictionaries representing the query results.
        :param db_id:
        :param sql_query:
        :param current_user:
    """
    # Load the database into memory
    print(f"Loading database with ID {db_id} for user {current_user}")
    conn_info = load_db_by_id(current_user, db_id)

    print(f"Executing query: {sql_query}")
    # Execute the SQL query
    result_set = query_db(conn_info, sql_query)

    # Close the database connection
    conn, _ = conn_info
    conn.close()

    return result_set
