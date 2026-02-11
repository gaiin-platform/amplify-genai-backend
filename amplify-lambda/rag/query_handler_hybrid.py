"""
Hybrid Query Handler - Query API for Hybrid Search
Exposes REST API for searching documents with Hybrid Search (Dense + BM25)
"""

import json
import os
from typing import List, Dict, Optional
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

from embedding.embedding_hybrid import search_hybrid
from vdr.maxsim_search import search_vdr_documents, generate_query_embeddings, hybrid_vdr_text_search

logger = getLogger("query_handler_hybrid")


@required_env_vars({
    "RAG_POSTGRES_DB_READ_ENDPOINT": [],
    "RAG_POSTGRES_DB_USERNAME": [],
    "RAG_POSTGRES_DB_NAME": [],
    "RAG_POSTGRES_DB_SECRET": []
})
@track_execution(operation_name="query_documents_hybrid", account="user")
def query_documents_hybrid(event, context):
    """
    Query documents using Hybrid Search

    REST API endpoint for searching documents

    Request body:
    {
        "query": "search query text",
        "document_ids": ["doc1", "doc2"],  // Optional: search specific docs
        "top_k": 10,  // Optional: number of results
        "search_mode": "hybrid",  // Optional: "hybrid", "dense", "sparse", "vdr"
        "dense_weight": 0.7,  // Optional: weight for dense search
        "sparse_weight": 0.3,  // Optional: weight for sparse search
        "use_rrf": false  // Optional: use Reciprocal Rank Fusion
    }

    Response:
    {
        "results": [
            {
                "chunk_id": "uuid",
                "document_id": "uuid",
                "content": "chunk text",
                "score": 0.95,
                "page_num": 5,
                "pipeline": "text_rag" or "vdr",
                "metadata": {}
            }
        ],
        "query": "original query",
        "total_results": 10,
        "search_mode": "hybrid",
        "processing_time_ms": 250
    }
    """
    import time
    start_time = time.time()

    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))

        query = body.get('query')
        if not query or not query.strip():
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Query is required'})
            }

        document_ids = body.get('document_ids', [])
        top_k = body.get('top_k', 10)
        search_mode = body.get('search_mode', 'hybrid')
        dense_weight = body.get('dense_weight', 0.7)
        sparse_weight = body.get('sparse_weight', 0.3)
        use_rrf = body.get('use_rrf', False)

        # Extract user from authorization
        user_id = extract_user_from_event(event)

        logger.info(f"Query: '{query[:50]}...', mode: {search_mode}, user: {user_id}")

        # Get user's accessible documents
        if not document_ids:
            document_ids = get_user_documents(user_id)
        else:
            # Verify user has access to requested documents
            accessible_docs = get_user_documents(user_id)
            document_ids = [doc_id for doc_id in document_ids if doc_id in accessible_docs]

        if not document_ids:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'results': [],
                    'query': query,
                    'total_results': 0,
                    'message': 'No accessible documents found'
                })
            }

        logger.info(f"Searching {len(document_ids)} accessible documents")

        # Route to appropriate search method
        if search_mode == 'vdr':
            results = search_vdr_mode(query, document_ids, top_k)
        elif search_mode == 'hybrid_vdr_text':
            results = search_hybrid_vdr_text_mode(query, document_ids, top_k, dense_weight, sparse_weight)
        else:  # hybrid, dense, sparse
            results = search_text_rag_mode(
                query,
                document_ids,
                top_k,
                dense_weight,
                sparse_weight,
                use_rrf,
                search_mode
            )

        processing_time_ms = int((time.time() - start_time) * 1000)

        logger.info(f"Query complete: {len(results)} results in {processing_time_ms}ms")

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Credentials': True
            },
            'body': json.dumps({
                'results': results,
                'query': query,
                'total_results': len(results),
                'search_mode': search_mode,
                'processing_time_ms': processing_time_ms
            })
        }

    except Exception as e:
        logger.error(f"Query failed: {str(e)}")
        logger.exception(e)

        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def search_text_rag_mode(
    query: str,
    document_ids: List[str],
    top_k: int,
    dense_weight: float,
    sparse_weight: float,
    use_rrf: bool,
    search_mode: str
) -> List[Dict]:
    """
    Search Text RAG documents with Hybrid Search
    """
    all_results = []

    for doc_id in document_ids:
        try:
            doc_results = search_hybrid(
                query,
                doc_id,
                top_k=top_k,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                use_rrf=use_rrf
            )

            for result in doc_results:
                all_results.append({
                    'chunk_id': result['chunk_id'],
                    'document_id': doc_id,
                    'content': result['content'],
                    'score': result['score'],
                    'pipeline': 'text_rag',
                    'search_mode': search_mode
                })
        except Exception as e:
            logger.warning(f"Failed to search document {doc_id}: {str(e)}")
            continue

    # Sort by score and return top_k
    all_results.sort(key=lambda x: x['score'], reverse=True)
    return all_results[:top_k]


def search_vdr_mode(
    query: str,
    document_ids: List[str],
    top_k: int
) -> List[Dict]:
    """
    Search VDR documents with MaxSim
    """
    from vdr.vdr_pipeline import load_vdr_model

    # Load VDR model
    model_name = os.environ.get("VDR_MODEL_NAME", "ModernVBERT/modernvbert-base")
    vdr_model = load_vdr_model(model_name)

    # Generate query embeddings
    query_embeddings = generate_query_embeddings(query, vdr_model)

    # Search across documents
    all_results = []

    for doc_id in document_ids:
        try:
            doc_results = search_vdr_documents(
                query_embeddings,
                document_id=doc_id,
                top_k=top_k,
                page_level=True
            )

            for result_doc_id, page_num, score in doc_results:
                all_results.append({
                    'document_id': result_doc_id,
                    'page_num': page_num,
                    'score': float(score),
                    'pipeline': 'vdr',
                    'search_mode': 'vdr'
                })
        except Exception as e:
            logger.warning(f"Failed to search VDR document {doc_id}: {str(e)}")
            continue

    # Sort by score and return top_k
    all_results.sort(key=lambda x: x['score'], reverse=True)
    return all_results[:top_k]


def search_hybrid_vdr_text_mode(
    query: str,
    document_ids: List[str],
    top_k: int,
    vdr_weight: float,
    text_weight: float
) -> List[Dict]:
    """
    Search with hybrid VDR + Text RAG
    """
    from vdr.vdr_pipeline import load_vdr_model

    # Load VDR model
    model_name = os.environ.get("VDR_MODEL_NAME", "ModernVBERT/modernvbert-base")
    vdr_model = load_vdr_model(model_name)

    # Generate query embeddings
    query_embeddings = generate_query_embeddings(query, vdr_model)

    all_results = []

    for doc_id in document_ids:
        try:
            doc_results = hybrid_vdr_text_search(
                query,
                query_embeddings,
                doc_id,
                vdr_weight=vdr_weight,
                text_weight=text_weight,
                top_k=top_k
            )

            for result_type, content_id, score, page_num in doc_results:
                all_results.append({
                    'document_id': doc_id,
                    'content_id': content_id,
                    'result_type': result_type,
                    'page_num': page_num,
                    'score': float(score),
                    'pipeline': 'hybrid_vdr_text',
                    'search_mode': 'hybrid_vdr_text'
                })
        except Exception as e:
            logger.warning(f"Failed to search document {doc_id}: {str(e)}")
            continue

    # Sort by score and return top_k
    all_results.sort(key=lambda x: x['score'], reverse=True)
    return all_results[:top_k]


def extract_user_from_event(event: Dict) -> Optional[str]:
    """
    Extract user ID from Lambda event

    Checks:
    1. authorizer.claims.sub (Cognito)
    2. requestContext.authorizer.claims.sub (API Gateway)
    3. headers.Authorization (custom)
    """
    # Check authorizer
    authorizer = event.get('requestContext', {}).get('authorizer', {})
    if 'claims' in authorizer:
        return authorizer['claims'].get('sub')

    # Check custom header
    headers = event.get('headers', {})
    auth_header = headers.get('Authorization') or headers.get('authorization')
    if auth_header:
        # Parse JWT or custom token
        # This is simplified - real implementation would decode JWT
        return parse_user_from_token(auth_header)

    return None


def parse_user_from_token(token: str) -> Optional[str]:
    """
    Parse user ID from JWT token
    """
    try:
        import jwt

        # This is simplified - real implementation would verify signature
        decoded = jwt.decode(token.replace('Bearer ', ''), options={"verify_signature": False})
        return decoded.get('sub')
    except:
        return None


def get_user_documents(user_id: str) -> List[str]:
    """
    Get list of document IDs accessible by user

    Queries documents table filtered by user access
    """
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("RAG_POSTGRES_DB_READ_ENDPOINT"),
        database=os.environ.get("RAG_POSTGRES_DB_NAME"),
        user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
        password=os.environ.get("RAG_POSTGRES_DB_SECRET")
    )

    cursor = conn.cursor()

    # Query user's accessible documents
    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 1000
        """,
        (user_id,)
    )

    document_ids = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return document_ids


@required_env_vars({
    "RAG_POSTGRES_DB_READ_ENDPOINT": []
})
@track_execution(operation_name="get_document_info", account="user")
def get_document_info(event, context):
    """
    Get document metadata

    GET /documents/{document_id}

    Response:
    {
        "document_id": "uuid",
        "bucket": "s3-bucket",
        "key": "s3-key",
        "pipeline": "vdr" or "text_rag",
        "num_chunks": 100,
        "num_pages": 50,
        "created_at": "2025-01-01T00:00:00Z",
        "user_id": "user-uuid"
    }
    """
    try:
        document_id = event.get('pathParameters', {}).get('document_id')

        if not document_id:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'document_id is required'})
            }

        user_id = extract_user_from_event(event)

        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("RAG_POSTGRES_DB_READ_ENDPOINT"),
            database=os.environ.get("RAG_POSTGRES_DB_NAME"),
            user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
            password=os.environ.get("RAG_POSTGRES_DB_SECRET")
        )

        cursor = conn.cursor()

        # Get document metadata
        cursor.execute(
            """
            SELECT
                d.id,
                d.bucket,
                d.key,
                d.pipeline_type,
                d.user_id,
                d.created_at,
                (SELECT COUNT(*) FROM chunks WHERE document_id = d.id) as num_chunks,
                (SELECT COUNT(*) FROM document_vdr_pages WHERE document_id = d.id) as num_pages
            FROM documents d
            WHERE d.id = %s
            """,
            (document_id,)
        )

        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if not result:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Document not found'})
            }

        doc_id, bucket, key, pipeline_type, doc_user_id, created_at, num_chunks, num_pages = result

        # Check access
        if user_id and doc_user_id != user_id:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Access denied'})
            }

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'document_id': doc_id,
                'bucket': bucket,
                'key': key,
                'pipeline': pipeline_type,
                'num_chunks': num_chunks,
                'num_pages': num_pages,
                'created_at': created_at.isoformat() if created_at else None,
                'user_id': doc_user_id
            })
        }

    except Exception as e:
        logger.error(f"Get document info failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
