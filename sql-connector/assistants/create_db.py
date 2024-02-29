import os
import json
import boto3
import pandas as pd
import mysql.connector
from mysql.connector import errorcode

def infer_sql_data_type(pandas_dtype):
    print(f"Inferring SQL data type for pandas dtype: {pandas_dtype}")
    if pandas_dtype == 'int64':
        return 'INT'
    elif pandas_dtype == 'float64':
        return 'FLOAT'
    elif pandas_dtype == 'bool':
        return 'BOOLEAN'
    elif pandas_dtype == 'datetime64[ns]':
        return 'DATETIME'
    else:
        return 'VARCHAR(255)'  # Default to VARCHAR for other types including objects

def lambda_handler(event, context):
    print("Starting lambda_handler")
    host = ''
    port = 3306
    user = ''
    password = ''
    database = ''
    
    # Extracting bucket and key from the event
    print("Extracting bucket and key from the event")
    bucket_name = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    print(f"Bucket: {bucket_name}, Key: {key}")
    
    # Download the CSV file from S3
    print("Downloading CSV file from S3")
    s3 = boto3.client('s3')
    local_file = '/tmp/' + os.path.basename(key)
    try:
        s3.download_file(bucket_name, key, local_file)
        print("Download complete")
    except Exception as e:
        print(f"Failed to download file from S3: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Failed to download CSV file from S3.')
        }
    
    # Read the CSV file into a pandas DataFrame
    print("Reading the CSV file into a pandas DataFrame")
    try:
        df = pd.read_csv(local_file)
        print("CSV file read into DataFrame")
    except Exception as e:
        print(f"Failed to read CSV file into DataFrame: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Failed to read CSV into DataFrame.')
        }
    
    # Generate the CREATE TABLE SQL statement
    column_definitions = ', '.join([f'`{col}` {infer_sql_data_type(dtype.name)}' for col, dtype in df.dtypes.items()])
    create_table_query = f'CREATE TABLE IF NOT EXISTS my_table ({column_definitions});'
    
    # Connect to your AWS RDS database
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()
        print("Connected to the database")
        
        # Create table using the generated SQL
        cursor.execute(create_table_query)
        
        # Insert DataFrame records one by one
        for i, row in df.iterrows():
            placeholders = ', '.join(['%s'] * len(row))
            insert_query = f'INSERT INTO my_table VALUES ({placeholders})'
            try:
                cursor.execute(insert_query, tuple(row))
                conn.commit()
                print(f"Inserted row {i}")
            except Exception as e:
                print(f"Failed to insert row {i}: {e}")
        
    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Something is wrong with your user name or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Database does not exist")
        else:
            print("Error code:", err.errno)
            print("SQLSTATE value:", err.sqlstate)
            print("Error message:", err.msg)
        return {
            'statusCode': 500,
            'body': json.dumps('Failed to connect to the database.')
        }
    except Exception as e:
        print(f"An error occurred while connecting to the database: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Failed to connect to the database due to an unexpected error.')
        }
    else:
        print("Closing database connection")
        cursor.close()
        conn.close()
    
    print("Lambda function completed successfully")
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully inserted CSV data into RDS.')
    }