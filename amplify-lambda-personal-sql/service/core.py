import os
import uuid
import datetime
import boto3
import requests

from common.errors import ActionError
from common.ops import op
# Don't remove, required for the registry to work
from db.postgres import postgres_handler

from common.validate import validated
from botocore.exceptions import ClientError

from db.local_db import create_and_save_db_for_user, describe_schemas_from_temp_db
from db.query import llm_chat_query_db, describe_schemas_from_user_db, query_db_by_id, describe_schemas_from_db, \
    convert_schema_dicts_to_text, query_db

import db.registry as registry

dynamodb = boto3.resource('dynamodb')
files_bucket = os.environ['ASSISTANTS_FILES_BUCKET_NAME']


def parse_result(result):
    # Split the string into 'thought' and 'sql' parts
    thought_part, sql_part = result.split('sql:')

    # Extract the 'thought' value
    thought_value = thought_part.split('thought:')[1].strip().strip('"')

    # Extract the 'sql' value
    sql_value = sql_part.strip().strip('"')

    # Return the dictionary with 'thought' and 'sql' keys
    return sql_value, thought_value


@op(
    path="/pdb/sql/llmquery",
    name="queryDatabaseWithLLM",
    tags=["pdb"],
    description="Use a LLM to query a database for you based on a detailed natural langauge question, task,"
                " analysis or other text that you give it. It will create the SQL and return the results as text.",
    params={
        "id": "The ID of the database to query",
        "query": "A detailed natural langauge question, task, analysis or other description that the LLM will use to construct the SQL query. It should be completely self-contained and have all relevant information to construct the query. The LLM will use this to generate the SQL query based on its knowledge of the database.",
    }
)
@validated(op="query")
def llm_query_db(event, context, current_user, name, data):
    try:
        access_token = data['access_token']
        data = data['data']
        db_id = data['id']
        task = data['query']

        default_options = {
            'account': 'default',
            'model': os.getenv('DEFAULT_LLM_QUERY_MODEL'),
            'limit': 25
        }

        options = data.get('options', default_options)
        options = {**default_options, **options}

        account = options.get('account', os.getenv('DEFAULT_ACCOUNT'))
        model = options.get('model', os.getenv('DEFAULT_LLM_QUERY_MODEL'))

        max_tries = 3
        result = None
        tries = max_tries

        conn = registry.load_db_by_id(current_user, db_id)

        print(f"Querying database with ID {db_id} using LLM for user {current_user} with Model {model}")
        dbschema = describe_schemas_from_db(conn)
        print(f"Database schema JSON fetched")
        dbschema = convert_schema_dicts_to_text(dbschema)
        print(f"Database schema created in text")

        final_query = None
        final_thought = None

        while result is None and tries > 0:
            try:
                print(f"Using LLM to create query for: {current_user}/{db_id} with task: {task} (Tries left: {tries})")
                tries -= 1
                llm_query = llm_chat_query_db(current_user, access_token, account, db_id, dbschema, task, model)
                sql, thought = parse_result(llm_query)
                print(f"Thought: {thought}")
                print(f"SQL: {sql}")

                result = query_db(conn, sql)
                final_query = sql
                final_thought = thought

            except requests.exceptions.HTTPError as err:
                print(f'HTTP error occurred: {err}')
                print(f'Status code: {err.response.status_code}')
                print(f'Response headers: {err.response.headers}')
                print(f'Response text: {err.response.text}')
                result = None

            except Exception as e:
                print(e)
                result = None

        return {
            'success': True,
            'data': result,
            'location': {"sql_query": final_query, "thought": final_thought}
        }

    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to query DB"
        }


@op(
    path="/pdb/sql/register",
    name="registerDatabase",
    tags=["pdb"],
    description="Register a database with the Personal Database service. This will allow you to query the database.",
    params={
        "name": "The name for the database [a-zA-Z0-9_-], which is for the user's reference",
        "description": "A description of the database",
        "type": "The type of database (postgres, mysql, etc.)",
        "tags": "A list of tags for the database",
        "connection": "The connection information for the database as a JSON in the format {\"name\":\"alumni\",\"host\":\"localhost\",\"port\":5432,\"user\":\"juleswhite\",\"s_password\":\"amplifytestdb\"} Note: this is the ACTUAL name of the database within the database server, not the name of the database in the Personal Database service.",
    }
)
@validated(op="register")
def register_db(event, context, current_user, name, data):
    try:
        access_token = data['access_token']
        event_data = data['data']
        db_name = event_data.get('name')
        description = event_data.get('description', '')
        db_type = event_data.get('type')
        tags = event_data.get('tags', [])
        db_data = event_data.get('connection', {})

        # attempt to get a handler for the type
        handler = None
        try:
            handler = registry.get_db_handler_for_type(db_type)
        except Exception as e:
            print(e)

        if handler is None:
            return {
                'success': False,
                'message': f"Unsupported database type"
            }

        db_id = f"{db_type}/{str(uuid.uuid4())}"
        timestamp = datetime.datetime.now().isoformat()

        registry.register_db(access_token, current_user, db_type, db_id, db_name, description, tags, timestamp, db_data)

        return {
            'success': True,
            'id': db_id
        }
    except registry.DatabaseExistsError as e:
        print(e)
        return {
            'success': False,
            'message': f"Database with name {e.db_name} already exists for user {e.user}"
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to register DB"
        }


@op(
    path="/pdb/sql/create",
    name="createDatabaseFromCSVFiles",
    tags=["pdb"],
    description="Create a new database from a set of CSV files, one CSV file per table. The schema of each table will match the CSV file automatically.",
    params={
        "name": "The name for the database [a-zA-Z0-9_-], which is for the user's reference",
        "tables": "A JSON list of the tables to create that maps keys (short IDs) to tables. Do NOT put the JSON in a string within quotes. The corresponding document will be used to populate the rows in the table. A sample of the JSON format is: [{\"table\": \"stores\",\"key\":\"#$1\"},{\"table\": \"sales\",\"key\": \"#$2\"},{\"table\": \"features\",\"key\": \"#$0\"}]",
        "description": "A description of the database",
        "tags": "A list of string tags for the database"
    }
)
@validated(op="create")
def create_db(event, context, current_user, name, data):
    try:
        access_token = data['access_token']
        event_data = data['data']
        s3_bucket = os.getenv('PERSONAL_SQL_S3_BUCKET')
        key_table_list = event_data.get('tables')
        db_name = event_data.get('name')
        description = event_data.get('description', '')
        tags = event_data.get('tags', [])

        # Each entry in key_table_list is {key:..., table:...}, iterate over the keys and make sure they arne't dicts,
        # if so, extract the id of the dict
        for entry in key_table_list:
            if isinstance(entry['key'], dict):
                entry['key'] = entry['key'].get('id')


        if not (s3_bucket and key_table_list and db_name and description):
            return {
                'success': False,
                'message': "Missing required parameters"
            }

        print(f"Creating DB for user: {current_user}")

        s3_files_bucket = os.getenv('ASSISTANTS_FILES_BUCKET_NAME')
        db_id = create_and_save_db_for_user(access_token, current_user, s3_bucket, s3_files_bucket, key_table_list, db_name, description, tags)

        print(f"DB created for {current_user} with ID: {db_id}")

        return {
            'success': True,
            'id': db_id
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to create DB"
        }


@op(
    path="/pdb/sql/list",
    name="listUserDBs",
    tags=["pdb"],
    description="List all of the DBs that the user has registered.",
    params={
    }
)
@validated(op="list")
def get_user_dbs(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']

        print(f"Fetching list of databases for user: {current_user}")

        # Call the function that performs the business logic
        user_dbs = registry.get_dbs_for_user(current_user)

        print(f"List of databases fetched for {current_user}")

        return {
            'success': True,
            'data': user_dbs
        }
    except ClientError as e:
        print("Error in get_user_dbs {e}")

        return {
            'success': False,
            'message': "Failed to get list of databases for the user"
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        print("Error in get_user_dbs {e}")

        return {
            'success': False,
            'message': str(e)
        }


@op(
    path="/pdb/sql/schema",
    name="describeDBSchema",
    tags=["pdb"],
    description="Describe the schema of the given DB.",
    params={
        "id": "The ID of the database to describe the schema for",
    }
)
@validated(op="describe")
def describe_personal_db_schema(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']
        db_id = event_data.get('id')

        print(f"Fetching schema descriptions for user: {current_user}")

        # Call the function that performs the business logic
        conn_info = registry.load_db_by_id(current_user, db_id)
        schema_descriptions = describe_schemas_from_db(conn_info)
        conn, _ = conn_info
        conn.close()

        print(f"Schema descriptions fetched for {current_user}/{db_id}")

        return {
            'success': True,
            'data': schema_descriptions
        }
    except ClientError as e:
        print("Error in describe_personal_db_schema")
        print(e)
        return {
            'success': False,
            'message': "Failed to describe schemas from DB"
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        print("Error in describe_personal_db_schema")
        print(e)
        return {
            'success': False,
            'message': "Failed to describe schemas from DB"
        }


@validated(op="describe")
def describe_db_schema(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        access_token = data['access_token']
        event_data = data['data']
        key_table_list = event_data.get('tables')

        if not files_bucket or not key_table_list:
            return {
                'success': False,
                'message': "Required parameters 'key_table_list' missing"
            }

        print(f"Fetching schema descriptions for user: {current_user}")

        # Call the function that performs the business logic
        schema_descriptions = describe_schemas_from_temp_db(current_user, access_token, files_bucket, key_table_list)

        print(f"Schema descriptions fetched for {current_user}")

        return {
            'success': True,
            'data': schema_descriptions
        }
    except ClientError:
        return {
            'success': False,
            'message': "Failed to describe schemas from DB"
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        return {
            'success': False,
            'message': "Failed to describe schemas from DB"
        }


@op(
    path="/pdb/sql/query",
    name="queryDBWithSQL",
    tags=["pdb"],
    description="Execute a SQL query against the DB.",
    params={
        "id": "The ID of the database to query",
        "query": "The SQL query to execute"
    }
)
@validated(op="query")
def query_personal_db(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']
        id = event_data.get('id')
        query = event_data.get('query')

        if not id or not query:
            return {
                'success': False,
                'message': "Required parameters 'id' missing"
            }

        print(f"Querying db for: {current_user}/{id}")

        # query the db
        # Call the function that performs the business logic
        query_result = query_db_by_id(current_user, id, query)

        return {
            'success': True,
            'data': query_result
        }
    except ClientError as e:
        print("Error in query_personal_db")
        print(e)
        return {
            'success': False,
            'message': "Failed to query DB"
        }
    except ActionError as e:
        print(e)
        return {
            'success': False,
            'message': e.message
        }
    except Exception as e:
        print("Error in query_personal_db")
        print(e)
        return {
            'success': False,
            'message': "Failed to query DB"
        }