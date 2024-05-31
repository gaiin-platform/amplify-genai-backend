import sqlite3
import pandas as pd
import boto3
from io import StringIO
import sqlite3
import sqlalchemy
import random
import datetime
import tempfile
import uuid
import time
import os
from dotenv import load_dotenv
from sqlalchemy.sql import text
from sqlalchemy import create_engine, inspect
import logging

s3 = boto3.client('s3')
dynamodb = boto3.client('dynamodb')

# DynamoDB lock table name
LOCK_TABLE_NAME = os.getenv('PERSONAL_SQL_DYNAMO_TABLE')
BUCKET_NAME = os.getenv('PERSONAL_SQL_S3_BUCKET')
FILES_BUCKET_NAME = os.getenv('ASSISTANTS_FILES_BUCKET_NAME')


def get_db_connection_wal():
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn


def load_csv_from_s3_to_db(s3_bucket, key_table_list, conn):
    """
    Load multiple CSV files from S3 to database tables

    Example usage:
    key_table_list = [
        {'table': 'table1', 'key': 's3_key1'},
        {'table': 'table2', 'key': 's3_key2'}
    ]
    load_csv_from_s3_to_db('my_s3_bucket', key_table_list, db_connection)

    Parameters:
    s3_bucket (str): The name of the S3 bucket
    key_table_list (list): List of dictionaries with 'table' and 'key'
    conn: Database connection
    """
    for item in key_table_list:
        table_name = item['table']
        s3_key = item['key']

        # Fetch the object from S3
        response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')

        # Read CSV content into DataFrame
        df = pd.read_csv(StringIO(csv_content))

        # Load DataFrame into the database
        df.to_sql(table_name, conn, if_exists='replace', index=False)


def save_db_to_s3(conn, s3_bucket, s3_key):
    temp_dir = tempfile.gettempdir()
    temp_db_path = os.path.join(temp_dir, f"sqlite-{datetime.datetime.now().isoformat()}.db")

    # Save database to a temporary file
    bck = sqlite3.connect(temp_db_path)
    with bck:
        conn.backup(bck)
    bck.close()

    # Upload the SQLite database file to S3
    s3.upload_file(temp_db_path, s3_bucket, s3_key)

    # Clean up the temporary file
    os.remove(temp_db_path)


def fetch_data_from_db(s3_bucket, key_table_list, sql_query):
    conn = get_db_connection_wal()

    # Step 2: Load CSVs from S3 to the in-memory DB for each key-table pair
    load_csv_from_s3_to_db(s3_bucket, key_table_list, conn)

    # Step 3: Execute the SQL query
    engine = sqlalchemy.create_engine('sqlite://', creator=lambda: conn)
    with engine.connect() as connection:
        result = connection.execute(text(sql_query))
        # Step 4: Fetch results as dictionary
        result_set = [dict(row) for row in result.mappings()]

    # Close the connection
    conn.close()

    return result_set


def describe_schemas_from_db(s3_bucket, key_table_list):
    conn = get_db_connection_wal()

    # Load CSVs from S3 to the in-memory DB for each key-table pair
    load_csv_from_s3_to_db(s3_bucket, key_table_list, conn)

    # Create an engine and inspector for schema inspection
    engine = sqlalchemy.create_engine('sqlite://', creator=lambda: conn)
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

    # Close the connection
    conn.close()

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


def insert_data_to_db(s3_bucket, s3_key, insert_sql, data_list):
    resource_id = f"{s3_bucket}/{s3_key}"

    lock_id = acquire_lock(resource_id)
    if not lock_id:
        return {"status": "error", "message": "Failed to acquire lock. Please try again."}

    try:
        # Connect to in-memory SQLite DB
        conn = get_db_connection_wal()

        # Load CSV from S3 to DB
        load_csv_from_s3_to_db(s3_bucket, s3_key, conn)

        # Prepare and execute insert statements
        cursor = conn.cursor()

        for data in data_list:
            cursor.execute(insert_sql, data)
        conn.commit()
        # Save the updated database back to S3
        save_db_to_s3(conn, s3_bucket, s3_key)
        conn.close()
    except Exception as e:
        # Release the lock in case of any exception
        release_lock(resource_id, lock_id)
        return {"status": "error", "message": str(e)}

    # Release the lock after successful DB update
    release_lock(resource_id, lock_id)
    return {"status": "success"}


def acquire_lock(resource_id, max_retries=4, retry_interval_range=(1, 2)):
    lock_id = str(uuid.uuid4())
    timestamp = int(time.time())

    for attempt in range(max_retries):
        try:
            dynamodb.put_item(
                TableName=LOCK_TABLE_NAME,
                Item={
                    'ResourceId': {'S': resource_id},
                    'LockId': {'S': lock_id},
                    'Timestamp': {'N': str(timestamp)}
                },
                ConditionExpression='attribute_not_exists(ResourceId) OR #t < :timeout',
                ExpressionAttributeNames={'#t': 'Timestamp'},
                ExpressionAttributeValues={':timeout': {'N': str(timestamp - 30)}}  # 30 seconds timeout
            )
            return lock_id
        except dynamodb.exceptions.ConditionalCheckFailedException:
            time.sleep(random.uniform(*retry_interval_range))
    return None


# Release the distributed lock
def release_lock(resource_id, lock_id):
    try:
        dynamodb.delete_item(
            TableName=LOCK_TABLE_NAME,
            Key={'ResourceId': {'S': resource_id}},
            ConditionExpression='LockId = :lock_id',
            ExpressionAttributeValues={':lock_id': {'S': lock_id}}
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        pass



# schemas = describe_schemas_from_db(test_bucket, [
#     {'table': 'stores', 'key': test_key},
#     {'table': 'sales', 'key': test_key2},
#     {'table': 'features', 'key': test_key3},
# ])
#
# print(convert_schema_dicts_to_text(schemas))
#
# results = fetch_data_from_db(test_bucket, [
#     {'table': 'stores', 'key': test_key},
#     {'table': 'sales', 'key': test_key2},
#     {'table': 'features', 'key': test_key3},
# ],
# """
# WITH FirstTenStores AS (
#     SELECT Store
#     FROM stores
#     ORDER BY Store
#     LIMIT 10
# )
#
# SELECT
#     f.Store,
#     f.Type AS Feature_Type,
#     f.Size AS Feature_Size,
#     s.Dept,
#     s.Date,
#     s.Weekly_Sales,
#     s.IsHoliday
# FROM
#     (SELECT Store, Type, Size FROM features WHERE Store IN (SELECT Store FROM FirstTenStores) LIMIT 10) AS f
# JOIN
#     sales AS s
# ON
#     f.Store = s.Store
# WHERE
#     s.Store IN (SELECT Store FROM FirstTenStores)
# LIMIT 100;
# """)
# print(results)