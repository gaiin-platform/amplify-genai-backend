import pandas as pd
import boto3
from io import StringIO
import sqlite3
import random
import datetime
import tempfile
import uuid
import time
import os

from db.query import query_db, describe_schemas_from_db
from db.registry import register_db, get_db_metadata, db_handler


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
        print(f"Loading {s3_key} from S3 to {table_name}")
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
    print(f"Saving database to {temp_db_path}")
    bck = sqlite3.connect(temp_db_path)
    with bck:
        conn.backup(bck)
    bck.close()

    print(f"Uploading database to S3 key {s3_bucket}/{s3_key}")
    # Upload the SQLite database file to S3
    s3.upload_file(temp_db_path, s3_bucket, s3_key)

    print(f"Database uploaded to S3 key {s3_bucket}/{s3_key}")

    # Clean up the temporary file
    os.remove(temp_db_path)


@db_handler('pdbs')
def pdbs_handler(current_user, db_id, metadata, data):
    creator = metadata.get('creator')
    if not creator:
        print(f"No creator found in metadata for database with id {db_id}")
        raise ValueError(f"No creator found in metadata for database with id {db_id}")

    if creator != current_user:
        print(f"Database with id {db_id} does not belong to user {current_user}")
        raise ValueError(f"Database with id {db_id} does not belong to user {current_user}")

    # Extract the S3 key from the metadata
    s3_key = metadata.get('data').get('s3Key')
    if not s3_key:
        print(f"No S3 key found in metadata for database with id {db_id}")
        raise ValueError(f"No S3 key found in metadata for database with id {db_id}")

    print(f"Loading database with ID {db_id} for user {current_user} from S3 key {s3_key}")

    # Get the S3 bucket name from environment variable
    s3_bucket = os.getenv('PERSONAL_SQL_S3_BUCKET')
    if not s3_bucket:
        raise ValueError("Environment variable 'PERSONAL_SQL_S3_BUCKET' is not set.")

    # Fetch the database file from S3
    s3_object = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    db_data = s3_object['Body'].read()

    print(f"Database contents fetched from S3 key {s3_key}")

    tmp_file = tempfile.NamedTemporaryFile(delete=False)
    tmp_file.write(db_data)
    tmp_file.flush()

    print(f"Database contents written to temporary file {tmp_file.name}")

    # Create in-memory SQLite database
    conn = sqlite3.connect(':memory:')

    try:
        # Create a temporary file-based SQLite connection
        file_conn = sqlite3.connect(tmp_file.name)

        # Backup the file-based database to the in-memory database
        file_conn.backup(conn)

        # Close the file-based connection
        file_conn.close()
    finally:
        # Clean up the temporary file
        os.unlink(tmp_file.name)

    print(f"Database loaded into memory for user {current_user} from S3 key {s3_key}")

    return conn, 'sqlite://'


def load_db_by_id(current_user, db_id):
    """
    Loads the SQLite database into memory by looking up its metadata in DynamoDB by ID,
    fetching the S3 data, and loading the in-memory DB from that S3 data.

    Args:
        db_id (str): The unique identifier of the database.

    Returns:
        sqlite3.Connection: The in-memory SQLite database connection.
        :param current_user:
    """
    metadata = get_db_metadata(current_user, db_id)
    return pdbs_handler(current_user, db_id, metadata, metadata.get('data'))


def fetch_data_from_db(s3_bucket, key_table_list, sql_query):
    conn = get_db_connection_wal()

    # Step 2: Load CSVs from S3 to the in-memory DB for each key-table pair
    load_csv_from_s3_to_db(s3_bucket, key_table_list, conn)

    conn_info = conn, 'sqlite://'
    # Step 3: Execute the SQL query
    result_set = query_db(conn_info, sql_query)
    conn.close()

    return result_set


def describe_schemas_from_temp_db(s3_bucket, key_table_list):
    conn = get_db_connection_wal()
    conn_info = conn, 'sqlite://'
    # Load CSVs from S3 to the in-memory DB for each key-table pair
    load_csv_from_s3_to_db(s3_bucket, key_table_list, conn)
    return describe_schemas_from_db(conn_info)


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


def create_and_save_db_for_user(current_user, s3_db_bucket, s3_files_bucket, key_table_list, db_name, description, tags):
    """
    Create a SQLite database, save it to S3 in a directory under the user, and return a unique identifier for the db.

    Args:
    current_user (str): The current user's username or ID.
    s3_bucket (str): The name of the S3 bucket.
    key_table_list (list): List of dictionaries with 'table' and 'key'.
    db_name (str): The name to assign to the created database.
    description (str): Description of the database.
    tags (dict): Dictionary of tags to associate with the database.

    Returns:
    str: A unique identifier for the created database.
    """
    # Create a unique identifier for the database
    db_id = f"pdbs/{str(uuid.uuid4())}"

    # Create in-memory SQLite database with WAL mode
    conn = get_db_connection_wal()

    # Load CSV files from S3 to the database
    load_csv_from_s3_to_db(s3_files_bucket, key_table_list, conn)

    # Create the path for saving the DB in S3
    timestamp = datetime.datetime.now().isoformat()
    user_directory = f"{current_user}/{db_id}/{timestamp}.db"

    # Save database to S3
    save_db_to_s3(conn, s3_db_bucket, user_directory)

    # Close the database connection
    conn.close()

    register_db(current_user, 'pdbs', db_id, db_name, description, tags, timestamp,
                {
                    'tables': key_table_list,
                    's3Key': user_directory
                })

    return db_id


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