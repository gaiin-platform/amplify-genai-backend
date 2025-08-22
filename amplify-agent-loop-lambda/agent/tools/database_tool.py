import os
import yaml
import logging
import re
import boto3
import json
from typing import Dict, Optional, List, Union, Tuple
from agent.components.tool import register_tool
from agent.core import ActionContext
from agent.prompt import Prompt

"""
Database Configuration from DynamoDB

This module uses AWS DynamoDB table 'amplify-v6-lambda-dev-db-connections' to store
database connection configurations. Each item in the table should have the following structure:

{
    "id": "unique_connection_identifier",
    "user": "user_id",              // Required: User ID to filter configurations
    "type": "snowflake|postgres|mysql|mssql|duckdb|bigquery|sqlite|oracle",
    "account": "account_identifier",  // for Snowflake
    "username": "username",           // for Snowflake
    "password": "password",           // for Snowflake
    "warehouse": "warehouse_name",    // for Snowflake
    "database": "database_name",
    "schema": "schema_name",
    "host": "host_address",          // for other databases
    "port": 5432,                    // for other databases
    "user": "username",              // for other databases
    "project": "project_id",         // for BigQuery
    "location": "location"           // for BigQuery
}

IMPORTANT: The "user" field is used to filter DB configurations stored in DynamoDB. 
Only configurations matching the current_user parameter will be loaded from DynamoDB.
"""


def load_config_from_dynamodb(current_user: str = None):
    """Load configuration from AWS DynamoDB table filtered by current user"""
    try:
        logging.info(
            f"Loading database configuration from DynamoDB for user: {current_user}"
        )
        # Initialize DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("DB_CONNECTIONS_TABLE"))

        # Scan the table to get all configurations
        response = table.scan()
        items = response.get("Items", [])
        logging.info(f"Found {len(items)} total items in DynamoDB table")

        # Process the items into a configuration structure
        config = {"db_config": {}}

        filtered_items = 0
        for item in items:
            # Filter by current user if provided
            if current_user and item.get("user") != current_user:
                continue
            filtered_items += 1

            # Assuming each item has an 'id' as the primary key
            # and contains database configuration details
            connection_id = item.get("id", "default")

            # Extract database configuration from the item
            db_config = {
                "db_type": item.get("type", "snowflake"),
                "account": item.get("account"),
                "username": item.get("username"),
                "password": item.get("password"),
                "warehouse": item.get("warehouse"),
                "database": item.get("database"),
                "schema": item.get("schema"),
                "host": item.get("host"),
                "port": item.get("port"),
                "user": item.get("user"),
                "password": item.get("password"),
                "project": item.get("project"),
                "location": item.get("location"),
            }

            # Remove None values
            db_config = {k: v for k, v in db_config.items() if v is not None}

            # Store in config structure
            config["db_config"][connection_id] = db_config

        logging.info(
            f"Processed {filtered_items} items matching user filter, found {len(config['db_config'])} database configurations"
        )

        # If no configurations found, raise an error
        if not config["db_config"]:
            error_msg = f"No database configurations found in DynamoDB table for user: {current_user}"
            logging.error(error_msg)
            raise ValueError(error_msg)

        return config

    except Exception as e:
        logging.error(f"Failed to load configuration from DynamoDB: {str(e)}")
        raise ValueError(
            f"Failed to load database configuration from DynamoDB: {str(e)}"
        )


def get_db_config(connection_id: str = "default", current_user: str = None) -> Dict:
    """Get specific database configuration by connection ID and current user"""
    logging.info(
        f"get_db_config called with connection_id: {connection_id}, current_user: {current_user}"
    )
    config = load_config_from_dynamodb(current_user)
    logging.info(f"Loaded config from DynamoDB: {config}")

    # If asking for "default", return the first available configuration for the user
    if connection_id == "default":
        if config["db_config"]:
            first_config = list(config["db_config"].values())[0]
            logging.info(
                f"Returning first available config for 'default': {first_config}"
            )
            return first_config
        else:
            logging.warning(
                f"No database configurations found for user: {current_user}"
            )
            return {}

    # Otherwise, look for the specific connection ID
    specific_config = config["db_config"].get(connection_id, {})
    logging.info(
        f"Returning specific config for connection_id {connection_id}: {specific_config}"
    )
    return specific_config


def load_config(current_user: str = None):
    """Load configuration - now uses DynamoDB instead of YAML file"""
    return load_config_from_dynamodb(current_user)


@register_tool(
    tags=["default", "database", "data", "query", "sql"],
    status="querying_database",
    resultStatus="database_query_complete",
    errorStatus="database_query_error",
)
def query_database(
    question: str,
    connection_id: str,
    action_context: Optional[ActionContext] = None,
) -> Dict:
    """
    Query a database using natural language. This tool can:
    - Convert natural language questions into SQL queries
    - Execute SQL queries against various database types (Snowflake, PostgreSQL, MySQL, etc.)
    - Return structured data results
    - Provide explanations of the results
    - Handle complex queries involving multiple tables and joins
    - Support aggregations, filtering, and sorting
    - Use database configurations stored in AWS DynamoDB

    Parameters:
        question (str): The natural language question to ask about the data
        connection_id (str, optional): The connection ID to use from DynamoDB configuration.
            If not provided, the tool will automatically use any database connections
            attached to the current conversation.
        action_context (ActionContext, optional): System context (automatically provided)

    Returns:
        Dict containing:
        - success (bool): Whether the query was successful
        - data (List[Dict]): The query results as a list of dictionaries
        - error (str): Error message if the query failed
        - sql (str): The generated SQL query
        - explanation (str): Natural language explanation of the results
        - relevant_tables (List[str]): List of tables used in the query
        - relevant_columns (List[str]): List of columns used in the query

    Example usage:
        query_database(
            question="What are the top 10 products by revenue?",
            connection_id="connection_id_here"
        )
    """

    # Add comprehensive error handling from the start
    logging.info(
        f"Database tool starting execution with question: {question}, connection_id: {connection_id}"
    )

    # try to import vanna modules directly (will fail in slim container)
    try:
        from vanna.base import VannaBase
        from vanna.chromadb import ChromaDB_VectorStore

        logging.info("Successfully imported Vanna modules")
    except ImportError as e:
        logging.error(f"Failed to import Vanna modules: {e}")
        return {
            "success": False,
            "error": f"Failed to import required Vanna modules: {str(e)}. Fat container is not available. Please ensure Vanna is installed.",
            "sql": None,
            "data": None,
            "explanation": None,
            "relevant_tables": [],
            "relevant_columns": [],
        }

    # Configure logging to suppress Vanna's verbose output
    logging.getLogger("vanna").setLevel(logging.WARNING)

    class AmplifyLLM(VannaBase):
        def __init__(self, config=None, action_context=None):
            self.config = config or {}
            self.action_context = action_context
            if not self.action_context:
                logging.error("action_context is required for AmplifyLLM")
                raise ValueError("action_context is required for AmplifyLLM")
            logging.info("Successfully initialized AmplifyLLM with action_context")
            self.db_schema = None
            self.prompt_history = []  # Track prompt history for context

        def set_db_schema(self, schema_info):
            """Set the database schema information for the system message"""
            self.db_schema = schema_info

        def system_message(self, question: str = None, sql: str = None) -> str:
            if not self.db_schema:
                return """You are a SQL expert that helps generate SQL queries based on natural language questions.
                You will be provided with database schema information when available."""

            # Analyze the question to determine relevant tables and columns
            relevant_schema = (
                self._get_relevant_schema(question) if question else self.db_schema
            )

            schema_text = "You have access to a database with the following schema:\n\n"
            for table_name, columns in relevant_schema.items():
                schema_text += f"Table: {table_name}\nColumns:\n"
                for col in columns:
                    schema_text += f"- {col}\n"
                schema_text += "\n"

            return f"""You are a SQL expert that helps generate SQL queries based on natural language questions.
            {schema_text}
            When asked about data, use the appropriate tables and columns from the schema above.
            Always include relevant columns in your SELECT statements.
            Never use placeholder table names like 'your_table_name' - use the actual table names from the schema.
            
            Consider the following when generating queries:
            1. Use appropriate JOINs when data spans multiple tables
            2. Include relevant WHERE clauses based on the question
            3. Use appropriate aggregations (COUNT, SUM, AVG, etc.) when needed
            4. Consider performance implications of the query
            5. Use appropriate ORDER BY clauses when sorting is implied
            6. Include LIMIT clauses when appropriate
            """

        def _get_relevant_schema(self, question: str) -> dict:
            """Analyze the question to determine relevant tables and columns."""
            relevant_schema = {}

            # Convert question to lowercase for case-insensitive matching
            question_lower = question.lower()

            # Look for table and column mentions in the question
            for table_name, columns in self.db_schema.items():
                table_lower = table_name.lower()

                # Check if table name is mentioned in question
                if table_lower in question_lower:
                    relevant_schema[table_name] = []

                    # Check for column mentions
                    for col in columns:
                        col_name = col.split(" ")[
                            0
                        ].lower()  # Get column name without type
                        if col_name in question_lower:
                            relevant_schema[table_name].append(col)

                    # If no specific columns mentioned, include all columns
                    if not relevant_schema[table_name]:
                        relevant_schema[table_name] = columns

            # If no tables found, return all schema
            return relevant_schema if relevant_schema else self.db_schema

        def user_message(self, question: str, sql: str = None) -> str:
            # Add question to prompt history
            self.prompt_history.append(question)

            # Build context from recent history
            context = ""
            if len(self.prompt_history) > 1:
                context = "Previous questions:\n"
                for prev_q in self.prompt_history[-3:-1]:  # Last 2 questions
                    context += f"- {prev_q}\n"
                context += "\n"

            return f"""{context}Generate a SQL query to answer this question: {question}
            
            Consider the following:
            1. What specific data is being requested?
            2. Are there any implicit conditions or filters?
            3. What kind of aggregation or grouping might be needed?
            4. Should the results be ordered in any way?
            5. Are there any performance considerations?
            """

        def assistant_message(self, question: str, sql: str) -> str:
            if not sql:
                return "I cannot generate a SQL query for this question."
            return f"Here's the SQL query to answer your question:\n{sql}"

        def submit_prompt(self, prompt, **kwargs) -> str:
            system_msg = self.system_message()
            
            if isinstance(prompt, list):
                prompt = "\n".join(prompt)

            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ]

            try:
                generate_response = self.action_context.get("llm")
                if not generate_response:
                    logging.error("No LLM available in action_context")
                    return None
                    
                response = generate_response(Prompt(messages=messages))
                return response
            except Exception as e:
                logging.error(f"Error in submit_prompt: {str(e)}")
                return None

        def generate_sql(self, question: str, **kwargs) -> str:
            """Generate SQL for a given question"""
            prompt = self.user_message(question)
            response = self.submit_prompt(prompt)
            if not response:
                return None

            sql_match = re.search(
                r"```sql\n(.*?)\n```|SQL query:\s*(.*?)(?:\n|$)", response, re.DOTALL
            )
            if sql_match:
                sql = sql_match.group(1) or sql_match.group(2)
                sql = sql.replace("```sql", "").replace("```", "").strip()
                if sql.lower().startswith(
                    ("select", "insert", "update", "delete", "create", "alter", "drop")
                ):
                    return sql
            return None

    class MyVanna(ChromaDB_VectorStore, AmplifyLLM):
        def __init__(self, config=None, action_context=None):
            config = config or {}
            config["persist_directory"] = None  # Use in-memory storage
            ChromaDB_VectorStore.__init__(self, config=config)
            AmplifyLLM.__init__(self, config=config, action_context=action_context)

    try:
        # Get current user from action context if available
        current_user = None
        if action_context:
            current_user = action_context.get("current_user")

        logging.info(
            f"Database tool called with connection_id: {connection_id}, current_user: {current_user}"
        )

        # If no connection_id provided, try to get it from action_context (attached databases)
        if not connection_id and action_context:
            # Check for attached database connection ID in action context
            attached_db_id = action_context.get("attached_database_connection_id")
            if attached_db_id:
                connection_id = attached_db_id
                logging.info(
                    f"Using attached database connection ID from action context: {connection_id}"
                )
            else:
                # Fallback: check for attachedDatabases array
                attached_dbs = action_context.get("attachedDatabases")
                if (
                    attached_dbs
                    and isinstance(attached_dbs, list)
                    and len(attached_dbs) > 0
                ):
                    connection_id = attached_dbs[0]
                    logging.info(
                        f"Using first attached database from action context: {connection_id}"
                    )

        # Check if we have a valid connection_id
        if not connection_id:
            return {
                "success": False,
                "error": "No connection ID provided and no attached database connections found. Please attach a database connection to the conversation or provide a connection_id parameter.",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Get database configuration from DynamoDB
        try:
            logging.info(
                f"Looking up database configuration for connection_id: {connection_id}, current_user: {current_user}"
            )
            db_config_from_dynamo = get_db_config(connection_id, current_user)
            logging.info(f"Database configuration retrieved: {db_config_from_dynamo}")

            # Check if configuration is empty
            if not db_config_from_dynamo:
                error_msg = f"No database configuration found for connection_id: {connection_id} and user: {current_user}"
                logging.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "sql": None,
                    "data": None,
                    "explanation": None,
                    "relevant_tables": [],
                    "relevant_columns": [],
                }
        except ValueError as e:
            logging.error(f"ValueError retrieving database config: {e}")
            return {
                "success": False,
                "error": str(e),
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }
        except Exception as e:
            logging.error(f"Unexpected error retrieving database config: {e}")
            return {
                "success": False,
                "error": f"Unexpected error retrieving database configuration: {str(e)}",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Extract database type from configuration
        db_type = db_config_from_dynamo.get("db_type", "snowflake")

        # Create Vanna instance with optimized configuration
        try:
            logging.info(f"Creating Vanna instance for database type: {db_type}")
            vn = MyVanna(
                config={
                    "model": "gpt-4o",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "db_type": db_type,
                },
                action_context=action_context
            )
            logging.info("Successfully created Vanna instance")
        except Exception as e:
            logging.error(f"Failed to create Vanna instance: {e}")
            return {
                "success": False,
                "error": f"Failed to initialize Vanna: {str(e)}",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Connect to the appropriate database
        db_connection_methods = {
            "postgres": vn.connect_to_postgres,
            "mssql": vn.connect_to_mssql,
            "mysql": vn.connect_to_mysql,
            "duckdb": vn.connect_to_duckdb,
            "snowflake": vn.connect_to_snowflake,
            "bigquery": vn.connect_to_bigquery,
            "sqlite": lambda **kwargs: vn.connect_to_sqlite(kwargs["database"]),
            "oracle": vn.connect_to_oracle,
        }

        if db_type not in db_connection_methods:
            return {
                "success": False,
                "error": f"Unsupported database type: {db_type}",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Remove None values from configuration
        db_config = {k: v for k, v in db_config_from_dynamo.items() if v is not None}
        logging.info(f"Cleaned database config: {db_config}")

        # Connect to database using configuration from DynamoDB
        try:
            logging.info(f"Attempting to connect to {db_type} database")
            db_connection_methods[db_type](**db_config)
            logging.info("Successfully connected to database")
        except Exception as e:
            logging.error(f"Failed to connect to database: {e}")
            return {
                "success": False,
                "error": f"Failed to connect to {db_type} database: {str(e)}",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Get database and schema from configuration
        database = db_config.get("database")
        schema = db_config.get("schema")

        # Get schema information
        schema_query = get_schema_query(db_type, database, schema)
        if not schema_query:
            return {
                "success": False,
                "error": f"Schema query not implemented for database type: {db_type}",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        df_information_schema = vn.run_sql(schema_query)

        # Process schema information
        schema_info = process_schema_info(df_information_schema, db_type)
        vn.set_db_schema(schema_info)

        # Train the model with schema information
        try:
            # Generate and execute training plan
            plan = vn.get_training_plan_generic(df_information_schema)
            if plan:
                vn.train(plan=plan)

            # Add DDL schema
            add_ddl_schema(vn, database, schema, df_information_schema)

            # Add documentation
            add_documentation(vn, df_information_schema)

            # Add example queries
            add_example_queries(vn, database, schema, df_information_schema)

        except Exception as e:
            logging.warning(
                f"Training step failed: {str(e)}. Continuing with query generation..."
            )

        # Generate and execute SQL
        sql = vn.generate_sql(question)
        if not sql:
            return {
                "success": False,
                "error": "Failed to generate SQL query",
                "sql": None,
                "data": None,
                "explanation": None,
                "relevant_tables": [],
                "relevant_columns": [],
            }

        # Extract relevant tables and columns from the SQL
        relevant_tables, relevant_columns = extract_relevant_objects(
            sql, database, schema
        )

        # Clean and validate SQL
        clean_sql = clean_and_validate_sql(sql, database, schema)

        # Execute the query
        results = vn.run_sql(clean_sql)

        # Generate explanation with context
        explanation = generate_explanation(
            vn, question, clean_sql, results, relevant_tables, relevant_columns
        )

        # Convert results to list of dictionaries
        data = results.to_dict("records")

        logging.info(
            f"Database tool completed successfully. Returned {len(data)} rows of data."
        )
        return {
            "success": True,
            "data": data,
            "error": None,
            "sql": clean_sql,
            "explanation": explanation,
            "relevant_tables": relevant_tables,
            "relevant_columns": relevant_columns,
        }

    except Exception as e:
        logging.error(f"Unexpected error in database tool: {e}")
        logging.error(f"Exception type: {type(e).__name__}")
        import traceback

        logging.error(f"Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": f"Unexpected error in database tool: {str(e)}",
            "sql": None,
            "data": None,
            "explanation": None,
            "relevant_tables": [],
            "relevant_columns": [],
        }


def get_schema_query(db_type: str, database: str, schema: str) -> str:
    """Get the appropriate schema query based on database type."""
    schema_queries = {
        "snowflake": f"""
            SELECT * FROM {database}.INFORMATION_SCHEMA.COLUMNS
            WHERE table_schema = '{schema}'
        """,
        "postgres": f"""
            SELECT 
                table_name,
                column_name,
                data_type,
                character_maximum_length,
                column_default,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = '{schema}'
            ORDER BY table_name, ordinal_position
        """,
        "mysql": f"""
            SELECT 
                table_name,
                column_name,
                data_type,
                character_maximum_length,
                column_default,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = '{database}'
            ORDER BY table_name, ordinal_position
        """,
    }
    return schema_queries.get(db_type)


def process_schema_info(df_information_schema, db_type: str) -> dict:
    """Process schema information into a format suitable for the LLM."""
    schema_info = {}
    unique_tables = df_information_schema["TABLE_NAME"].unique()

    for table_name in unique_tables:
        table_columns = df_information_schema[
            df_information_schema["TABLE_NAME"] == table_name
        ]

        for _, row in table_columns.iterrows():
            column_name = row["COLUMN_NAME"]

            if db_type == "snowflake":
                column_info = f"{column_name} ({row['DATA_TYPE']})"
            elif db_type in ["postgres", "mysql"]:
                data_type = row["DATA_TYPE"]
                max_length = row["CHARACTER_MAXIMUM_LENGTH"]
                is_nullable = row["IS_NULLABLE"]
                default = row["COLUMN_DEFAULT"]

                column_info = f"{column_name} ({data_type}"
                if max_length:
                    column_info += f"({max_length})"
                column_info += ")"

                if is_nullable == "NO":
                    column_info += " NOT NULL"
                if default:
                    column_info += f" DEFAULT {default}"
            else:
                column_info = f"{column_name} ({row['COLUMN_TYPE']})"

            if table_name not in schema_info:
                schema_info[table_name] = []
            schema_info[table_name].append(column_info)

    return schema_info


def add_ddl_schema(vn, database: str, schema: str, df_information_schema):
    """Add DDL schema information to the model."""
    unique_tables = df_information_schema["TABLE_NAME"].unique()

    for table_name in unique_tables:
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
        except Exception as e:
            logging.warning(f"Could not add DDL for table {table_name}: {str(e)}")


def add_documentation(vn, df_information_schema):
    """Add documentation to the model."""
    unique_tables = df_information_schema["TABLE_NAME"].unique()
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

    try:
        vn.train(documentation=documentation)
    except Exception as e:
        logging.warning(f"Could not add documentation: {str(e)}")


def add_example_queries(vn, database: str, schema: str, df_information_schema):
    """Add example queries to the model."""
    unique_tables = df_information_schema["TABLE_NAME"].unique()

    for table_name in unique_tables:
        columns = df_information_schema[
            df_information_schema["TABLE_NAME"] == table_name
        ]["COLUMN_NAME"].tolist()
        example_columns = columns[:5]  # Use first 5 columns

        if example_columns:
            example_query = f"""
            SELECT 
                {', '.join(example_columns)}
            FROM {database}.{schema}.{table_name}
            LIMIT 5;
            """
            try:
                vn.train(sql=example_query)
            except Exception as e:
                logging.warning(
                    f"Could not add example query for {table_name}: {str(e)}"
                )


def clean_and_validate_sql(sql: str, database: str, schema: str) -> str:
    """Clean and validate the generated SQL query."""
    # Remove markdown formatting
    clean_sql = sql.replace("```sql", "").replace("```", "").strip()

    # Ensure table names are fully qualified
    if not clean_sql.upper().startswith(f"SELECT * FROM {database}.{schema}."):
        # Replace unqualified table names with fully qualified ones
        clean_sql = re.sub(
            r"FROM\s+([a-zA-Z0-9_]+)(?=\s|$)",
            f"FROM {database}.{schema}.\\1",
            clean_sql,
        )

    return clean_sql


def extract_relevant_objects(
    sql: str, database: str, schema: str
) -> Tuple[List[str], List[str]]:
    """Extract relevant tables and columns from SQL query."""
    tables = []
    columns = []

    # Extract tables
    table_pattern = rf"FROM\s+{database}\.{schema}\.([a-zA-Z0-9_]+)"
    tables = re.findall(table_pattern, sql, re.IGNORECASE)

    # Extract columns
    column_pattern = r"SELECT\s+(.*?)\s+FROM"
    column_match = re.search(column_pattern, sql, re.IGNORECASE)
    if column_match:
        columns_str = column_match.group(1)
        # Split by comma and clean up
        columns = [col.strip().split(".")[-1] for col in columns_str.split(",")]

    return tables, columns


def generate_explanation(
    vn,
    question: str,
    sql: str,
    results,
    relevant_tables: List[str],
    relevant_columns: List[str],
) -> str:
    """Generate a natural language explanation of the query results."""
    try:
        explanation_prompt = f"""
        Question: {question}
        SQL Query: {sql}
        Results: {results.to_string()}
        Tables Used: {', '.join(relevant_tables)}
        Columns Used: {', '.join(relevant_columns)}
        
        Please provide a natural language explanation of these results, focusing on:
        1. The key insights and patterns in the data
        2. How the results relate to the original question
        3. Any notable trends or anomalies
        4. The significance of the selected tables and columns
        """
        explanation = vn.submit_prompt(explanation_prompt)
        return explanation if explanation else "Results retrieved successfully."
    except Exception as e:
        logging.warning(f"Could not generate explanation: {str(e)}")
        return "Results retrieved successfully."
