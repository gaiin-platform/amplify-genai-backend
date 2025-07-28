import os
import requests
import json
import logging
import yaml
from dotenv import load_dotenv
from vanna.base import VannaBase
from vanna.chromadb import ChromaDB_VectorStore

# Configure logging to suppress Vanna's verbose output
logging.getLogger("vanna").setLevel(logging.WARNING)


def load_config():
    """Load configuration from YAML file"""
    # Get the workspace root directory (two levels up from the script location)
    workspace_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    config_path = os.path.join(workspace_root, "var", "dev-var.yml")
    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        raise ValueError(f"Failed to load configuration from {config_path}: {str(e)}")


# Load configuration
config = load_config()
DB_CONFIG = config["db_config"]

# Set environment variables from config
os.environ["AMPLIFY_API_KEY"] = config["api_keys"]["amplify"]

# Load environment variables
load_dotenv()


class AmplifyLLM(VannaBase):
    def __init__(self, config=None):
        self.config = config or {}
        self.api_key = os.getenv("AMPLIFY_API_KEY")
        if not self.api_key:
            raise ValueError("AMPLIFY_API_KEY not found in environment variables")
        self.db_schema = None

    def set_db_schema(self, schema_info):
        """Set the database schema information for the system message"""
        self.db_schema = schema_info

    def system_message(self, question: str = None, sql: str = None) -> str:
        if not self.db_schema:
            return """You are a SQL expert that helps generate SQL queries based on natural language questions.
            You will be provided with database schema information when available."""

        schema_text = "You have access to a database with the following schema:\n\n"
        for table_name, columns in self.db_schema.items():
            schema_text += f"Table: {table_name}\nColumns:\n"
            for col in columns:
                schema_text += f"- {col}\n"
            schema_text += "\n"

        return f"""You are a SQL expert that helps generate SQL queries based on natural language questions.
        {schema_text}
        When asked about data, use the appropriate tables and columns from the schema above.
        Always include relevant columns in your SELECT statements.
        Never use placeholder table names like 'your_table_name' - use the actual table names from the schema."""

    def user_message(self, question: str, sql: str = None) -> str:
        return f"Generate a SQL query to answer this question: {question}"

    def assistant_message(self, question: str, sql: str) -> str:
        if not sql:
            return "I cannot generate a SQL query for this question."
        return f"Here's the SQL query to answer your question:\n{sql}"

    def submit_prompt(self, prompt, **kwargs) -> str:
        url = "https://prod-api.vanderbilt.ai/chat"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Format the prompt with system message
        system_msg = self.system_message()
        formatted_prompt = f"{system_msg}\n\n{prompt}"

        # Ensure prompt is a string
        if isinstance(prompt, list):
            prompt = "\n".join(prompt)

        payload = {
            "data": {
                "model": self.config.get("model", "gpt-4o"),
                "temperature": self.config.get("temperature", 0.1),
                "max_tokens": self.config.get("max_tokens", 4096),
                "dataSources": [],
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "options": {
                    "ragOnly": False,
                    "skipRag": True,
                    "model": {"id": self.config.get("model", "gpt-4o")},
                    "prompt": formatted_prompt,
                },
            }
        }

        try:
            response = requests.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                print(f"Error: {response.status_code} - {response.text}")

            response.raise_for_status()
            return response.json().get("data", "")
        except Exception as e:
            print(f"Error in submit_prompt: {str(e)}")
            if hasattr(e, "response"):
                print(f"Error details: {e.response.text}")
            return None

    def generate_sql(self, question: str, **kwargs) -> str:
        """Generate SQL for a given question"""
        prompt = self.user_message(question)
        response = self.submit_prompt(prompt)
        if not response:
            return None

        # Extract SQL from the response
        # Look for SQL between backticks or after "SQL query:"
        import re

        sql_match = re.search(
            r"```sql\n(.*?)\n```|SQL query:\s*(.*?)(?:\n|$)", response, re.DOTALL
        )
        if sql_match:
            sql = sql_match.group(1) or sql_match.group(2)
            # Clean up any remaining markdown or formatting
            sql = sql.replace("```sql", "").replace("```", "").strip()
            # Validate that it's actually a SQL query
            if sql.lower().startswith(
                ("select", "insert", "update", "delete", "create", "alter", "drop")
            ):
                return sql
        return None

    def ask(self, question: str) -> str:
        """Ask a question and get SQL response"""
        sql = self.generate_sql(question)
        if not sql:
            return None
        return sql


class MyVanna(ChromaDB_VectorStore, AmplifyLLM):
    def __init__(self, config=None):
        config = config or {}
        config["persist_directory"] = (
            None  # This will make ChromaDB use in-memory storage
        )
        ChromaDB_VectorStore.__init__(self, config=config)
        AmplifyLLM.__init__(self, config=config)


def test_amplify_vanna():
    """Test the Amplify Vanna implementation"""

    # Create Vanna instance with custom configuration
    config = {"model": "gpt-4o", "temperature": 0.1, "max_tokens": 4096}
    vn = MyVanna(config=config)

    # Connect to the database based on type
    db_type = config.get("db_type", "snowflake")

    try:
        if db_type == "postgres":
            vn.connect_to_postgres(**DB_CONFIG["postgres"])
        elif db_type == "mssql":
            vn.connect_to_mssql(**DB_CONFIG["mssql"])
        elif db_type == "mysql":
            vn.connect_to_mysql(**DB_CONFIG["mysql"])
        elif db_type == "duckdb":
            vn.connect_to_duckdb(**DB_CONFIG["duckdb"])
        elif db_type == "snowflake":
            vn.connect_to_snowflake(**DB_CONFIG["snowflake"])
        elif db_type == "bigquery":
            vn.connect_to_bigquery(**DB_CONFIG["bigquery"])
        elif db_type == "sqlite":
            vn.connect_to_sqlite(DB_CONFIG["sqlite"]["database"])
        elif db_type == "oracle":
            vn.connect_to_oracle(**DB_CONFIG["oracle"])
        else:
            raise ValueError(f"Unsupported database type: {db_type}")

        # Get information schema for all tables
        print("Getting information schema for all tables...")

        # Get database configuration
        db_config = DB_CONFIG[db_type]
        database = db_config["database"]
        schema = db_config["schema"]

        # Adjust the information schema query based on database type
        if db_type == "snowflake":
            schema_query = f"""
            SELECT * FROM {database}.INFORMATION_SCHEMA.COLUMNS
            WHERE table_schema = '{schema}'
            """
        else:
            raise ValueError(f"Unsupported database type for schema query: {db_type}")

        df_information_schema = vn.run_sql(schema_query)

        # Process schema information for the LLM
        schema_info = {}
        # Get all unique tables from the schema
        unique_tables = df_information_schema["TABLE_NAME"].unique()
        # Limit to 3 tables
        unique_tables = unique_tables[:3]
        print(
            f"Processing {len(unique_tables)} tables in schema {schema} (limited to 3 tables)"
        )

        for table_name in unique_tables:
            print(f"Processing table: {table_name}")
            # Get all columns for this table
            table_columns = df_information_schema[
                df_information_schema["TABLE_NAME"] == table_name
            ]

            for _, row in table_columns.iterrows():
                column_name = row["COLUMN_NAME"]

                # Handle different database types' column naming conventions
                if db_type == "snowflake":
                    column_info = f"{column_name} ({row['DATA_TYPE']})"
                else:
                    column_info = f"{column_name} ({row['COLUMN_TYPE']})"
                if table_name not in schema_info:
                    schema_info[table_name] = []
                schema_info[table_name].append(column_info)

        # Set the schema information in the LLM
        vn.set_db_schema(schema_info)

        # Generate training plan
        print("Generating and executing training plan...")
        try:
            # Use the full information schema for training plan generation
            plan = vn.get_training_plan_generic(df_information_schema)
            if plan:
                print("Training on schema...")
                vn.train(plan=plan)
                print("Training completed")
            else:
                print("Warning: No training plan generated")
        except Exception as e:
            print(f"Warning: Could not generate training plan: {str(e)}")
            print("Continuing with schema training...")

        # Add DDL schema
        print("Adding DDL schema...")
        try:
            # Get table information for only our limited set of tables
            tables_query = f"""
            SELECT TABLE_NAME
            FROM {database}.INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = '{schema}'
            AND TABLE_NAME IN ({','.join([f"'{t}'" for t in unique_tables])})
            """
            tables = vn.run_sql(tables_query)

            for idx, table_row in tables.iterrows():
                table_name = table_row["TABLE_NAME"]
                # Get columns for this table
                columns_query = f"""
                SELECT 
                    COLUMN_NAME,
                    DATA_TYPE,
                    IS_NULLABLE,
                    COLUMN_DEFAULT
                FROM {database}.INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = '{schema}'
                AND TABLE_NAME = '{table_name}'
                ORDER BY ORDINAL_POSITION
                """
                columns = vn.run_sql(columns_query)

                # Build DDL
                ddl = f"CREATE TABLE {table_name} (\n"
                column_defs = []
                for _, col in columns.iterrows():
                    col_def = f"    {col['COLUMN_NAME']} {col['DATA_TYPE']}"
                    if col["IS_NULLABLE"] == "NO":
                        col_def += " NOT NULL"
                    if col["COLUMN_DEFAULT"]:
                        col_def += f" DEFAULT {col['COLUMN_DEFAULT']}"
                    column_defs.append(col_def)
                ddl += ",\n".join(column_defs)
                ddl += "\n);"

                try:
                    vn.train(ddl=ddl)
                    print(f"Successfully added DDL for {table_name}")
                except Exception as e:
                    print(
                        f"Warning: Could not add DDL for table {table_name}: {str(e)}"
                    )
        except Exception as e:
            print(f"Warning: Could not generate DDL statements: {str(e)}")

        # Add documentation
        print("Adding documentation...")
        try:
            documentation = "Database Schema Documentation:\n\n"
            for table_name in unique_tables:
                table_docs = df_information_schema[
                    df_information_schema["TABLE_NAME"] == table_name
                ]
                documentation += f"Table: {table_name}\n"
                documentation += "Description: Table in the database\n"
                documentation += "Columns:\n"
                for _, row in table_docs.iterrows():
                    documentation += f"- {row['COLUMN_NAME']} ({row['DATA_TYPE']}): {row.get('COLUMN_COMMENT', 'No description available')}\n"
                documentation += "\n"
            vn.train(documentation=documentation)
        except Exception as e:
            print(f"Warning: Could not add documentation: {str(e)}")

        # Add example queries
        print("\nTraining with example queries...")
        try:
            # Create a set to track added queries
            added_queries = set()

            for table_name in unique_tables:
                # Get column names for this table
                columns = df_information_schema[
                    df_information_schema["TABLE_NAME"] == table_name
                ]["COLUMN_NAME"].tolist()

                # Create a dynamic example query using the first 5 columns
                example_columns = columns[:5]
                if example_columns:  # Only create query if we have columns
                    example_query = f"""
                    SELECT 
                        {', '.join(example_columns)}
                    FROM {database}.{schema}.{table_name}
                    LIMIT 5;
                    """

                    try:
                        vn.train(sql=example_query)
                        added_queries.add(example_query)
                        print(f"Added example query for {table_name}")
                    except Exception as e:
                        print(
                            f"Warning: Error during example query training for table {table_name}: {str(e)}"
                        )
        except Exception as e:
            print(f"Warning: Error during example query generation: {str(e)}")

        print("Schema training completed.")

        # Test a simple question for each table
        for table_name in unique_tables:
            # Get the columns we used in the example query for this table
            table_columns = df_information_schema[
                df_information_schema["TABLE_NAME"] == table_name
            ]["COLUMN_NAME"].tolist()[
                :5
            ]  # Use first 5 columns as in example query

            # Create fully qualified table name
            fully_qualified_table = f"{database}.{schema}.{table_name}"

            question = (
                f"Show me the first 5 rows from the {fully_qualified_table} table"
            )
            print(f"\nTesting query for {table_name}: {question}")

            # Use ask() instead of generate_sql()
            result = vn.ask(question=question)
            if not result:
                print(
                    f"\nCould not generate a valid SQL query for {table_name}. Trying a more specific question..."
                )
                # Try a more specific question with the actual table name and columns
                columns_str = ", ".join(table_columns)
                question = f"Show me the first 5 rows of {columns_str} from the {fully_qualified_table} table"
                print(f"\nTesting query: {question}")
                result = vn.ask(question=question)

            if result:
                print(f"\nGenerated SQL:\n{result}")
                try:
                    # Clean up any remaining markdown or formatting
                    clean_sql = result.replace("```sql", "").replace("```", "").strip()

                    # Ensure the table name is fully qualified
                    if not clean_sql.upper().startswith(
                        f"SELECT * FROM {database}.{schema}."
                    ):
                        clean_sql = clean_sql.replace(
                            f"FROM {table_name}", f"FROM {fully_qualified_table}"
                        ).replace(
                            f"FROM {schema}.{table_name}",
                            f"FROM {fully_qualified_table}",
                        )

                    # Print the final SQL for debugging
                    print(f"\nExecuting SQL:\n{clean_sql}")

                    results = vn.run_sql(clean_sql)
                    print(f"\nQuery Results:\n{results}")
                except Exception as e:
                    print(f"Error running query: {str(e)}")
                    print(f"\nTrying to list columns for table {table_name}...")
                    try:
                        columns = vn.run_sql(f"SHOW COLUMNS IN {fully_qualified_table}")
                        print("\nAvailable columns:")
                        print(columns)
                    except Exception as e2:
                        print(f"Error listing columns: {str(e2)}")
            else:
                print(f"\nCould not generate a valid SQL query for {table_name}.")

    except Exception as e:
        print(f"Error during database operations: {str(e)}")
        import traceback

        print(f"Full error traceback:\n{traceback.format_exc()}")


if __name__ == "__main__":
    test_amplify_vanna()
