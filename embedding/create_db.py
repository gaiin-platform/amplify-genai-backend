import psycopg2
from psycopg2 import sql

# Database connection parameters
db_params = {
    'dbname': 'your_database_name',
    'user': 'your_database_user',
    'password': 'your_database_password',
    'host': 'your_database_host',
    'port': 'your_database_port',  # Default PostgreSQL port is 5432
}

# SQL command to create the embeddings table
create_table_command = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embeddings (
    id BIGSERIAL PRIMARY KEY,
    src VARCHAR(255),
    locations jsonb,
    orig_indexes jsonb,
    char_index INTEGER[],
    token_count INTEGER,
    embedding_index INTEGER,
    owner_email VARCHAR(255),
    content TEXT,
    vector_embedding vector(1536),
    qa_vector_embedding vector(1536)
    content_tsvector TSVECTOR

);
"""

# Create Indexes
create INDEX on embeddings USING hnsw (vector_embedding vector_ip_ops) WITH (m = 16, ef_construction = 64);

## Create a trigger function: This function will be called whenever a row is inserted or updated in your table.
CREATE OR REPLACE FUNCTION update_tsvector_column() RETURNS trigger AS $$
BEGIN
  NEW.content_tsvector := to_tsvector('english', coalesce(NEW.content, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

# Create a trigger: The trigger will associate your table with the trigger function you've just defined, specifying that it should be fired before an insert or update operation.
CREATE TRIGGER content_vector_update BEFORE INSERT OR UPDATE
ON embeddings FOR EACH ROW EXECUTE FUNCTION update_tsvector_column();


# Create Index on tsvector column
CREATE INDEX content_vector_idx ON embeddings USING GIN (content_tsvector);





# Establish a connection to the database
try:
    conn = psycopg2.connect(**db_params)
    cur = conn.cursor()
    # Execute the SQL command
    cur.execute(sql.SQL(create_table_command))
    # Commit the transaction
    conn.commit()
    print("Table 'embeddings' created successfully.")
except psycopg2.Error as e:
    print(f"An error occurred: {e}")
finally:
    # Close the cursor and connection
    if cur:
        cur.close()
    if conn:
        conn.close()