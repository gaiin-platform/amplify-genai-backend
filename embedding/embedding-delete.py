import os
import psycopg2
from common.credentials import get_credentials
from common.validate import validated
import logging
import boto3
from boto3.dynamodb.conditions import Key
from common.ops import op

# Configure Logging 
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('embedding_delete')

# Get environment variables
pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
object_access_table = os.environ['OBJECT_ACCESS_TABLE']

# Define the permission levels that grant delete access
permission_levels = ['write', 'owner']

pg_password = get_credentials(rag_pg_password)

def check_delete_access(src_id, current_user):
    """Check if user has permission to delete embeddings for a given source."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(object_access_table)
    
    try:
        response = table.query(
            KeyConditionExpression=Key('object_id').eq(src_id) & Key('principal_id').eq(current_user)
        )
        
        # Check if user has write or owner permissions
        items_with_access = [item for item in response.get('Items', []) 
                           if item['permission_level'] in permission_levels]
        
        return len(items_with_access) > 0
        
    except Exception as e:
        logger.error(f"Error checking delete access: {e}")
        return False

def delete_embeddings_from_db(src_id):
    """Delete all embeddings for a given source from the database."""
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        with conn.cursor() as cur:
            try:
                # Delete both regular and QA embeddings for the source
                sql_query = """
                    DELETE FROM embeddings 
                    WHERE src = %s
                """
                cur.execute(sql_query, (src_id,))
                rows_deleted = cur.rowcount
                conn.commit()
                return rows_deleted
                
            except Exception as e:
                logger.error(f"Error deleting embeddings: {e}")
                conn.rollback()
                raise

@op(
    path="/embedding-delete",
    name="deleteEmbeddings",
    method="POST",
    tags=["apiDocumentation"],
    description="""Delete embeddings from Amplify data sources.

    Example request:
    {
        "data": {
            "dataSources": ["global/09342587234089234890.content.json"]
        }
    }

    Example response:
    {
        "result": {
            "deletedSources": ["global/09342587234089234890.content.json"],
            "failedSources": [],
            "totalDeleted": 10
        }
    }
    """,
    params={
        "dataSources": "Array of strings. Required. List of data source IDs to delete embeddings from. These ids must start with global/. Example: ['global/09342587234089234890.content.json']."
    }
)
@validated("embedding-delete")
def delete_embeddings(event, context, current_user, name, data):
    data = data['data']
    src_ids = data['dataSources']
    
    deleted_sources = []
    failed_sources = []
    total_deleted = 0
    
    for src_id in src_ids:
        try:
            # Check if user has permission to delete
            if not check_delete_access(src_id, current_user):
                logger.warning(f"User {current_user} does not have permission to delete embeddings for {src_id}")
                failed_sources.append(src_id)
                continue
                
            # Delete embeddings
            rows_deleted = delete_embeddings_from_db(src_id)
            total_deleted += rows_deleted
            deleted_sources.append(src_id)
            logger.info(f"Successfully deleted {rows_deleted} embeddings for source {src_id}")
            
        except Exception as e:
            logger.error(f"Failed to delete embeddings for source {src_id}: {e}")
            failed_sources.append(src_id)
    
    return {
        "result": {
            "deletedSources": deleted_sources,
            "failedSources": failed_sources,
            "totalDeleted": total_deleted
        }
    } 