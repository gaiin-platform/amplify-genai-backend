"""
BM25 Indexer with PostgreSQL Storage
Stores BM25 inverted index for fast lexical retrieval
"""

import psycopg2
from psycopg2.extras import execute_batch, Json
import json
import os
from typing import List, Dict, Tuple
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

from embedding.hybrid_search import tokenize_text, compute_term_frequencies

logger = getLogger("bm25_indexer")


@required_env_vars({
    "RAG_POSTGRES_DB_WRITE_ENDPOINT": [],
    "RAG_POSTGRES_DB_USERNAME": [],
    "RAG_POSTGRES_DB_NAME": [],
    "RAG_POSTGRES_DB_SECRET": []
})
def get_db_connection():
    """Get PostgreSQL database connection"""
    conn = psycopg2.connect(
        host=os.environ.get("RAG_POSTGRES_DB_WRITE_ENDPOINT"),
        database=os.environ.get("RAG_POSTGRES_DB_NAME"),
        user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
        password=os.environ.get("RAG_POSTGRES_DB_SECRET")
    )
    return conn


def index_document_bm25(
    document_id: str,
    chunks: List[Dict]
) -> Dict[str, int]:
    """
    Index document chunks for BM25 retrieval

    Stores:
    1. Chunk term frequencies
    2. Chunk lengths
    3. Global term statistics (document frequency)

    Args:
        document_id: Document UUID
        chunks: List of chunk dicts with 'id', 'content', 'embedding'

    Returns:
        Dict: Statistics (num_chunks, total_terms, unique_terms)
    """
    logger.info(f"Indexing {len(chunks)} chunks for BM25...")

    conn = get_db_connection()
    cursor = conn.cursor()

    global_term_counts: Dict[str, int] = {}
    total_terms = 0
    chunk_data = []

    for chunk in chunks:
        chunk_id = chunk['id']
        content = chunk['content']

        tokens = tokenize_text(content)
        term_freqs = compute_term_frequencies(tokens)
        doc_length = len(tokens)

        for term in term_freqs:
            global_term_counts[term] = global_term_counts.get(term, 0) + 1

        total_terms += doc_length

        chunk_data.append({
            'chunk_id': chunk_id,
            'term_freqs': term_freqs,
            'doc_length': doc_length
        })

    logger.info(f"Computed term frequencies: {total_terms} total terms, {len(global_term_counts)} unique")

    execute_batch(
        cursor,
        """
        INSERT INTO chunk_bm25_index (chunk_id, term_frequencies, doc_length)
        VALUES (%s, %s, %s)
        ON CONFLICT (chunk_id)
        DO UPDATE SET term_frequencies = EXCLUDED.term_frequencies,
                      doc_length = EXCLUDED.doc_length
        """,
        [(cd['chunk_id'], Json(cd['term_freqs']), cd['doc_length']) for cd in chunk_data]
    )

    logger.info(f"Stored BM25 index for {len(chunk_data)} chunks")

    cursor.execute(
        """
        SELECT term, document_frequency
        FROM bm25_term_stats
        WHERE document_id = %s
        """,
        (document_id,)
    )

    existing_stats = dict(cursor.fetchall())

    updated_stats = []
    for term, doc_freq in global_term_counts.items():
        current_freq = existing_stats.get(term, 0)
        new_freq = current_freq + doc_freq
        updated_stats.append((document_id, term, new_freq))

    execute_batch(
        cursor,
        """
        INSERT INTO bm25_term_stats (document_id, term, document_frequency)
        VALUES (%s, %s, %s)
        ON CONFLICT (document_id, term)
        DO UPDATE SET document_frequency = EXCLUDED.document_frequency
        """,
        updated_stats
    )

    logger.info(f"Updated term statistics for {len(updated_stats)} unique terms")

    cursor.execute(
        """
        UPDATE document_bm25_metadata
        SET
            total_chunks = %s,
            avg_chunk_length = %s,
            total_unique_terms = %s,
            updated_at = NOW()
        WHERE document_id = %s
        """,
        (
            len(chunks),
            total_terms / len(chunks) if chunks else 0,
            len(global_term_counts),
            document_id
        )
    )

    if cursor.rowcount == 0:
        cursor.execute(
            """
            INSERT INTO document_bm25_metadata (document_id, total_chunks, avg_chunk_length, total_unique_terms)
            VALUES (%s, %s, %s, %s)
            """,
            (
                document_id,
                len(chunks),
                total_terms / len(chunks) if chunks else 0,
                len(global_term_counts)
            )
        )

    conn.commit()
    cursor.close()
    conn.close()

    logger.info(f"✓ BM25 indexing complete for document {document_id}")

    return {
        'num_chunks': len(chunks),
        'total_terms': total_terms,
        'unique_terms': len(global_term_counts)
    }


def search_bm25(
    query: str,
    document_id: str,
    top_k: int = 10,
    k1: float = 1.5,
    b: float = 0.75
) -> List[Tuple[str, float]]:
    """
    Search document using BM25

    Args:
        query: Search query
        document_id: Document UUID to search
        top_k: Number of results to return
        k1: BM25 k1 parameter
        b: BM25 b parameter

    Returns:
        List[Tuple[str, float]]: (chunk_id, bm25_score)
    """
    logger.info(f"BM25 search: '{query[:50]}...' in document {document_id}")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT total_chunks, avg_chunk_length
        FROM document_bm25_metadata
        WHERE document_id = %s
        """,
        (document_id,)
    )

    result = cursor.fetchone()
    if not result:
        logger.warning(f"No BM25 metadata found for document {document_id}")
        return []

    total_docs, avg_doc_length = result

    query_terms = tokenize_text(query)

    if not query_terms:
        logger.warning("No valid query terms after tokenization")
        return []

    cursor.execute(
        """
        SELECT c.chunk_id, c.term_frequencies, c.doc_length
        FROM chunks c
        WHERE c.document_id = %s
        """,
        (document_id,)
    )

    chunks = cursor.fetchall()

    if not chunks:
        logger.warning(f"No chunks found for document {document_id}")
        return []

    term_doc_freqs = {}
    for term in query_terms:
        cursor.execute(
            """
            SELECT document_frequency
            FROM bm25_term_stats
            WHERE document_id = %s AND term = %s
            """,
            (document_id, term)
        )
        result = cursor.fetchone()
        term_doc_freqs[term] = result[0] if result else 0

    cursor.close()
    conn.close()

    scores = []

    for chunk_id, term_freqs_json, doc_length in chunks:
        term_freqs = term_freqs_json if isinstance(term_freqs_json, dict) else json.loads(term_freqs_json)

        score = 0.0

        for term in query_terms:
            docs_with_term = term_doc_freqs.get(term, 0)

            if docs_with_term == 0:
                continue

            import math
            idf = math.log((total_docs - docs_with_term + 0.5) / (docs_with_term + 0.5))

            term_freq = term_freqs.get(term, 0)

            if term_freq == 0:
                continue

            numerator = term_freq * (k1 + 1)
            denominator = term_freq + k1 * (1 - b + b * (doc_length / avg_doc_length))

            score += idf * (numerator / denominator)

        scores.append((chunk_id, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    top_results = scores[:top_k]

    logger.info(f"BM25 search complete: {len(top_results)} results")

    return top_results


def delete_document_bm25_index(document_id: str):
    """
    Delete BM25 index for a document

    Args:
        document_id: Document UUID
    """
    logger.info(f"Deleting BM25 index for document {document_id}")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM chunk_bm25_index WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (document_id,))
    deleted_chunks = cursor.rowcount

    cursor.execute("DELETE FROM bm25_term_stats WHERE document_id = %s", (document_id,))
    deleted_terms = cursor.rowcount

    cursor.execute("DELETE FROM document_bm25_metadata WHERE document_id = %s", (document_id,))
    deleted_metadata = cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    logger.info(f"Deleted BM25 index: {deleted_chunks} chunks, {deleted_terms} term stats, {deleted_metadata} metadata")
