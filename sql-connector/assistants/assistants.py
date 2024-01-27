from common.validate import validated
from common.llm import get_chat_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
import logging
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv


# TODO check if .env exists so that this doesn't have to be removed/added for local testing
# load_dotenv()  # Load environment variables from .env file
# Set the values below in a local .env file in the root of the project for local testing
mysql_host = os.environ["MYSQL_DB_HOST"]
mysql_user = os.environ["MYSQL_DB_USERNAME"]
mysql_database = os.environ["MYSQL_DB_NAME"]
mysql_password = os.environ["MYSQL_DB_PASSWORD"]
llm_endpoints = os.environ["LLM_ENDPOINTS_SECRETS_NAME"]


# Logging configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


# The DatabaseConnection class is a context manager for handling MySQL database connections.
# It simplifies the process of connecting to and disconnecting from the database, ensuring that
# connections are properly established and closed. This class uses the 'with' statement context
# manager protocol to automatically manage resources. When entering the context (using 'with'),
# it establishes a connection to the MySQL database using the provided credentials. Upon exiting
# the context, it ensures that the connection is closed, even if an error occurs during
# database operations. This approach prevents connection leaks and makes the code cleaner
# and more maintainable by abstracting the connection logic.
class DatabaseConnection:
    def __init__(self, host, database, user, password, port=3306):
        self.host = host
        self.database = database
        self.user = user
        self.password = password
        self.port = port
        self.connection = None

    def __enter__(self):
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                database=self.database,
                user=self.user,
                password=self.password,
                port=self.port,
            )
            logging.info("Database connection established.")
            return self.connection
        except Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logging.info("Database connection closed.")


# Function to establish a database connection
def get_db_connection():
    global db_connection
    if db_connection is None or not db_connection.is_connected():
        try:
            db_connection = mysql.connector.connect(
                host=mysql_host,
                database=mysql_database,
                user=mysql_user,
                password=mysql_password,
                port=3306,
            )
            logging.info("Database connection established.")
        except Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise
    return db_connection


# TODO implement validated
# @validated(op="execute_sql")
# TODO remove name,event,context param
def execute_sql_query(event, context, current_user, name, data):
    # something will read the event here
    # it will set this: data.get("user_prompt")

    user_prompt = data.get("user_prompt")
    max_retries = 3

    with DatabaseConnection(
        mysql_host, mysql_database, mysql_user, mysql_password
    ) as db_connection:
        schema_info = fetch_schema_info(db_connection)

        for attempt in range(max_retries):
            try:
                sql_query = generate_sql_query(user_prompt, schema_info, current_user)

                cleaned_sql_query = clean_sql_query(sql_query)

                result = execute_query(db_connection, cleaned_sql_query)

                return {"result": result}
            except Exception as e:
                logging.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise e  # Reraise the last exception after all retries have failed

    return {"result": result}


# Collects the database schema. This output will be used to provide the LLM with context of the database and generate a valid SQL query
def fetch_schema_info(db_connection):
    schema_info = {}
    try:
        with db_connection.cursor() as cursor:
            # Fetch all table names
            cursor.execute("SHOW TABLES;")
            tables = [table[0] for table in cursor.fetchall()]

            # Fetch columns for each table
            for table in tables:
                cursor.execute(f"DESCRIBE {table};")
                columns = cursor.fetchall()
                schema_info[table] = [
                    {"Field": col[0], "Type": col[1]} for col in columns
                ]

        logging.info("Schema information fetched.")
    except Exception as e:
        logging.error(f"Error fetching schema: {e}")
        raise

    return schema_info


# Calls the LLM to write the sql query (using the database schema and user's prompt as LLM input), but it does NOT execute the query
def generate_sql_query(user_prompt, schema_info, current_user):
    try:
        # Use LLM to generate SQL query based on user prompt and schema information
        # TODO make this a parameter
        llm = get_chat_llm("gpt-35-turbo")

        # Braces cause the input to the LLM to return an error, remove braces from schema_info
        clean_schema_info = (
            str(schema_info)
            .replace("[", "")
            .replace("]", "")
            .replace("{", "")
            .replace("}", "")
        )

        # if needed, add example rows from each table
        formatted_prompt = f"Given the database schema:\n\n{clean_schema_info}\n\nGenerate a SQL query for:\n\n{user_prompt}"

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an AI skilled in SQL. Generate a query based on the given schema and user request. Provide all SQL queries in markdown.",
                ),
                ("user", formatted_prompt),
            ]
        )

        output_parser = StrOutputParser()

        # Chain the components together
        chain = prompt | llm | output_parser

        # Log the prompt
        # logging.info(f"Sending prompt to LLM:\n{formatted_prompt}")

        # Invoke the chain with an empty input since the prompt already contains all necessary information
        return chain.invoke({"input": ""})

    except Exception as e:
        logging.error(f"Error in generate_sql_query: {e}")
        raise


def clean_sql_query(sql_query):
    # logging.info(f"SQL Query Before Cleaning:\n{sql_query}")

    # Step 1: Extract text after '```sql'
    start_index = sql_query.find("```sql")
    if start_index == -1:
        raise ValueError("The string '```sql' was not found in sql_query.")
    else:
        # Move past the '```sql'
        start_index += len("```sql")

        # Update sql_query to the substring after '```sql'
        sql_query = sql_query[start_index:]

    # Step 2: Extract text before the next '```'
    end_index = sql_query.find("```")
    if end_index == -1:
        raise ValueError("The closing '```' was not found in sql_query.")
    else:
        # Update sql_query to the substring before '```'
        sql_query = sql_query[:end_index]

    # logging.info(f"Cleaned SQL Query:\n{sql_query}")

    # Final cleaned SQL query
    return sql_query.strip()


# executes the query written by the generate_sql_query() function
def execute_query(connection, sql_query):
    try:
        # Execute the SQL query and return results
        with connection.cursor() as cursor:
            cursor.execute(sql_query)
            result = cursor.fetchall()
            logging.info(f"SQL Query Sent To DB:\n{sql_query}")
        return result
    except Exception as e:
        logging.error(f"Error executing query:\n{sql_query}\nException: {e}")
        raise
