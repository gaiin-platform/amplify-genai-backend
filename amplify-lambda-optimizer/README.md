# Amplify Personal SQL DB

## HTTP Request Paths

| **HTTP Path**                | **Method** | **Handler**                                | **Purpose**                                                                                         |
|------------------------------|------------|--------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `/pdb/sql/files/schema`      | POST       | `service/core.describe_db_schema`          | Describes the temporary database schema using the provided access token and a list of tables from an S3 bucket.                           |
| `/pdb/sql/schema`            | POST       | `service/core.describe_personal_db_schema` | Fetches schema descriptions for a personal user database based on the provided database ID.                                              |
| `/pdb/sql/list`              | POST       | `service/core.get_user_dbs`                | Fetches a list of all databases registered to the current user, retrieving database information associated with the user account.          |
| `/pdb/sql/create`            | POST       | `service/core.create_db`                   | Creates a new database for the user using the user's access token, table list, database name, description, and tags, combining data from an S3 bucket. |
| `/pdb/sql/register`          | POST       | `service/core.register_db`                 | Registers a new database with details such as the database name, description, type, tags, and connection information.                      |
| `/pdb/sql/llmquery`          | POST       | `service/core.llm_query_db`                | Queries a database using an LLM (Large Language Model) to generate the SQL query, leveraging the user's provided access token, task, and other options. |
| `/pdb/sql/query`             | POST       | `service/core.query_personal_db`           | Executes a query on a personal database by its ID and a provided SQL query, returning query results based on the execution of the SQL against the specified database. |

