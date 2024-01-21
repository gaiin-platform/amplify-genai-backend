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
    

    
);
"""
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