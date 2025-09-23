import os
import psycopg2
import logging
import boto3
from boto3.dynamodb.conditions import Key
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.EMBEDDING.value])

# Configure Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("embedding_delete")

# Get environment variables
pg_host = os.environ["RAG_POSTGRES_DB_WRITE_ENDPOINT"]
pg_user = os.environ["RAG_POSTGRES_DB_USERNAME"]
pg_database = os.environ["RAG_POSTGRES_DB_NAME"]
rag_pg_password = os.environ.get(
    "RAG_POSTGRES_DB_ENV", os.environ["RAG_POSTGRES_DB_SECRET"]
)
object_access_table = os.environ["OBJECT_ACCESS_TABLE"]

# Define the permission levels that grant delete access
permission_levels = ["write", "owner"]

# pg_password = get_credentials(rag_pg_password)


def check_delete_access(src_id, current_user):
    """Check if user has permission to delete embeddings for a given source."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(object_access_table)

    try:
        response = table.query(
            KeyConditionExpression=Key("object_id").eq(src_id)
            & Key("principal_id").eq(current_user)
        )
        logger.info(f"Access check response: {response}")  # Add this
        items_with_access = [
            item
            for item in response.get("Items", [])
            if item["permission_level"] in permission_levels
        ]
        logger.info(f"Items with access: {items_with_access}")  # Add this
        return len(items_with_access) > 0
    except Exception as e:
        logger.error(f"Error checking delete access: {e}")
        return False


def delete_embeddings_from_db(src_id, child_chunks=None):
    """
    Delete embeddings for a given source from the database.
    
    Args:
        src_id: The source document ID
        child_chunks: Optional list of specific chunks to delete. If None, deletes all chunks.
    """
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=rag_pg_password,
        port=3306,
    ) as conn:
        with conn.cursor() as cur:
            try:
                print(f"Connecting to database at {pg_host}")
                
                if child_chunks is None:
                    # Delete all embeddings for the source (original behavior)
                    sql_query = "DELETE FROM embeddings WHERE src = %s"
                    cur.execute(sql_query, (src_id,))
                    logger.info(f"[DELETE] Deleted all embeddings for source {src_id}")
                else:
                    # Delete only specific chunks
                    placeholders = ','.join(['%s'] * len(child_chunks))
                    sql_query = f"DELETE FROM embeddings WHERE src = %s AND child_chunk IN ({placeholders})"
                    cur.execute(sql_query, [src_id] + child_chunks)
                    logger.info(f"[DELETE] Deleted embeddings for chunks {child_chunks} of source {src_id}")
                
                rows_deleted = cur.rowcount
                conn.commit()
                return rows_deleted

            except Exception as e:
                logger.error(f"Error deleting embeddings: {e}")
                conn.rollback()
                raise


def delete_progress_entry(src_id):
    """Delete progress table entry for a given source."""
    try:
        dynamodb = boto3.resource("dynamodb")
        progress_table = os.environ.get("EMBEDDING_PROGRESS_TABLE")
        
        if not progress_table:
            logger.warning("EMBEDDING_PROGRESS_TABLE not configured, skipping progress cleanup")
            return True
            
        table = dynamodb.Table(progress_table)
        
        # First check if item exists
        try:
            response = table.get_item(Key={"object_id": src_id})
            if "Item" not in response:
                logger.info(f"No progress entry found for {src_id}")
                return True
        except Exception as e:
            logger.warning(f"Could not check progress entry for {src_id}: {e}")
            return True
        
        # Delete the progress entry
        table.delete_item(Key={"object_id": src_id})
        logger.info(f"Successfully deleted progress entry for {src_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting progress entry for {src_id}: {e}")
        return False


@validated("embedding-delete")
def delete_embeddings(event, context, current_user, name, data):
    data = data["data"]
    src_ids = data["dataSources"]

    deleted_sources = []
    failed_sources = []
    total_deleted = 0

    for src_id in src_ids:
        try:
            # Check if user has permission to delete
            if not check_delete_access(src_id, current_user):
                logger.warning(
                    f"User {current_user} does not have permission to delete embeddings for {src_id}"
                )
                failed_sources.append(src_id)
                continue

            # Delete embeddings from PostgreSQL
            rows_deleted = delete_embeddings_from_db(src_id)
            
            # Delete progress entry from DynamoDB
            progress_deleted = delete_progress_entry(src_id)
            
            total_deleted += rows_deleted
            deleted_sources.append(src_id)
            
            if progress_deleted:
                logger.info(f"Successfully deleted {rows_deleted} embeddings and progress entry for source {src_id}")
            else:
                logger.warning(f"Successfully deleted {rows_deleted} embeddings but failed to delete progress entry for source {src_id}")
            
        except Exception as e:
            logger.error(f"Failed to delete embeddings for source {src_id}: {e}")
            failed_sources.append(src_id)

    return {
        "result": {
            "deletedSources": deleted_sources,
            "failedSources": failed_sources,
            "totalDeleted": total_deleted,
        }
    }
