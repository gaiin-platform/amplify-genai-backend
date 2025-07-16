# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import psycopg2
from psycopg2 import sql
import os
import logging
import json
from pycommon.api.credentials import get_credentials

# Setting up logging to capture information for CloudWatch
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("create_db")


def create_table():

    # Retrieving environment variables
    pg_host = os.environ["RAG_POSTGRES_DB_WRITE_ENDPOINT"]
    pg_user = os.environ["RAG_POSTGRES_DB_USERNAME"]
    pg_database = os.environ["RAG_POSTGRES_DB_NAME"]
    rag_pg_password = os.environ["RAG_POSTGRES_DB_SECRET"]

    pg_password = get_credentials(rag_pg_password)

    # Database connection parameters
    db_params = {
        "dbname": pg_database,
        "user": pg_user,
        "password": pg_password,
        "host": pg_host,
        "port": 3306,  # Non-standard port for RDS PostgreSQL; this is intentional as per the user's environment
    }

    # SQL commands to create the embeddings table and related components
    create_table_command = """
    -- Enable extension for vector operations if it doesn't exist.
    CREATE EXTENSION IF NOT EXISTS vector;
    -- Create the embeddings table
    CREATE TABLE IF NOT EXISTS embeddings (
        id BIGSERIAL PRIMARY KEY,
        src VARCHAR(255),
        locations jsonb,
        orig_indexes jsonb,
        char_index INTEGER,
        token_count INTEGER,
        embedding_index INTEGER,
        owner_email VARCHAR(255),
        content TEXT,
        vector_embedding vector(1536),
        qa_vector_embedding vector(1536),
        content_tsvector TSVECTOR
    );
    -- Create an index on the 'vector_embedding' column using the hnsw method.
    CREATE INDEX embeddings_vector_embedding_hnsw_idx ON embeddings USING hnsw (vector_embedding vector_ip_ops) WITH (m = 16, ef_construction = 64);
    -- Create an index on the 'vector_embedding_qa' column using the hnsw method.
    CREATE INDEX embeddings_vector_qa_embedding_hnsw_idx ON embeddings USING hnsw (qa_vector_embedding vector_ip_ops) WITH (m = 16, ef_construction = 64);
    -- Define the trigger function to update the 'content_tsvector' column before insert or update
    CREATE INDEX idx_src ON embeddings (src);
    CREATE OR REPLACE FUNCTION update_tsvector_column() RETURNS trigger AS $$
    BEGIN
    NEW.content_tsvector := to_tsvector('english', coalesce(NEW.content, ''));
    RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    -- Create a trigger that calls the trigger function before insert or update operations
    CREATE TRIGGER content_vector_update BEFORE INSERT OR UPDATE ON embeddings
    FOR EACH ROW EXECUTE FUNCTION update_tsvector_column();
    -- Create an index on the 'content_tsvector' column using the GIN method
    CREATE INDEX content_vector_idx ON embeddings USING GIN (content_tsvector);

    """

    # Establish a connection to the database and execute the commands
    try:
        logger.info(f"Connecting to database '{pg_database}' at '{pg_host}:{db_params['port']}' with user '{pg_user}'.")
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        logger.info("Connected successfully, executing SQL commands.")

        # Execute the SQL command
        cur.execute(sql.SQL(create_table_command))

        # Commit the transaction
        conn.commit()
        logger.info("SQL commands executed and committed successfully.")

    except psycopg2.Error as e:
        logger.error(f"An error occurred: {e}")
        if e.pgcode is not None:
            logger.error(f"PostgreSQL error code: {e.pgcode}")
        if e.diag.message_primary is not None:
            logger.error(f"PostgreSQL error message: {e.diag.message_primary}")
    finally:
        # Close the cursor and connection
        if cur:
            cur.close()
            logger.info("Cursor closed.")
        if conn:
            conn.close()
            logger.info("Database connection closed.")

    logger.info("Table 'embeddings' created successfully.")

    return {
        "statusCode": 200,
        "body": json.dumps("Lambda function executed successfully!"),
    }
