import psycopg2

from db.registry import db_handler


@db_handler('postgres')
def postgres_handler(current_user, db_id, metadata, data):
    try:
        print(f"Loading postgres db: {db_id}")

        db = data
        db_name = db["name"]
        db_user = db["user"]
        db_password = db["password"]
        db_host = db["host"]
        db_port = db["port"]

        print(f"Connecting to postgres db: {db_name} on {db_host}:{db_port}")

        # Define the connection parameters
        conn = psycopg2.connect(
            dbname=db_name,
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port
        )

        return conn, 'postgresql+psycopg2://'
    except Exception as error:
        print(f"Error connecting to postgres db: {error}")
        raise error

