"""
MaxSim (Maximum Similarity) Search for VDR
Late Interaction matching: query tokens × document patches

Used by ColPali and ModernVBERT for visual document retrieval
"""

import numpy as np
import psycopg2
from typing import List, Tuple, Dict
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars
import os
import json

logger = getLogger("maxsim_search")


def maxsim_score(
    query_embeddings: np.ndarray,
    doc_embeddings: np.ndarray
) -> float:
    """
    Compute MaxSim score between query and document

    MaxSim Formula:
    score(Q, D) = Σ max_j sim(qi, dj)

    For each query token embedding qi:
    1. Compute similarity with all document patch embeddings dj
    2. Take the maximum similarity
    3. Sum across all query tokens

    This captures fine-grained matching between query concepts and document regions

    Args:
        query_embeddings: Query token embeddings (shape: [num_query_tokens, embedding_dim])
        doc_embeddings: Document patch embeddings (shape: [num_patches, embedding_dim])

    Returns:
        float: MaxSim score
    """
    similarity_matrix = np.dot(query_embeddings, doc_embeddings.T)

    max_similarities = similarity_matrix.max(axis=1)

    maxsim = max_similarities.sum()

    return float(maxsim)


def maxsim_batch(
    query_embeddings: np.ndarray,
    doc_embeddings_list: List[np.ndarray]
) -> np.ndarray:
    """
    Compute MaxSim scores for query against multiple documents

    Optimized batch computation

    Args:
        query_embeddings: Query token embeddings (shape: [num_query_tokens, embedding_dim])
        doc_embeddings_list: List of document patch embeddings

    Returns:
        np.ndarray: MaxSim scores (shape: [num_docs])
    """
    scores = np.zeros(len(doc_embeddings_list))

    for idx, doc_embeddings in enumerate(doc_embeddings_list):
        scores[idx] = maxsim_score(query_embeddings, doc_embeddings)

    return scores


@required_env_vars({
    "RAG_POSTGRES_DB_WRITE_ENDPOINT": [],
    "RAG_POSTGRES_DB_USERNAME": [],
    "RAG_POSTGRES_DB_NAME": [],
    "RAG_POSTGRES_DB_SECRET": []
})
def search_vdr_documents(
    query_embeddings: np.ndarray,
    document_id: str = None,
    top_k: int = 10,
    page_level: bool = True
) -> List[Tuple[str, int, float]]:
    """
    Search VDR documents using MaxSim

    Args:
        query_embeddings: Query token embeddings (shape: [num_tokens, embedding_dim])
        document_id: Optional document ID to search within (None = search all)
        top_k: Number of results to return
        page_level: Return page-level results (True) or document-level (False)

    Returns:
        List[Tuple[str, int, float]]: (document_id, page_num, maxsim_score)
    """
    logger.info(f"VDR search with MaxSim: top_k={top_k}, page_level={page_level}")

    conn = psycopg2.connect(
        host=os.environ.get("RAG_POSTGRES_DB_WRITE_ENDPOINT"),
        database=os.environ.get("RAG_POSTGRES_DB_NAME"),
        user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
        password=os.environ.get("RAG_POSTGRES_DB_SECRET")
    )

    cursor = conn.cursor()

    if document_id:
        cursor.execute(
            """
            SELECT document_id, page_num, embedding_vectors
            FROM document_vdr_pages
            WHERE document_id = %s
            ORDER BY page_num
            """,
            (document_id,)
        )
    else:
        cursor.execute(
            """
            SELECT document_id, page_num, embedding_vectors
            FROM document_vdr_pages
            ORDER BY document_id, page_num
            LIMIT 1000
            """
        )

    pages = cursor.fetchall()

    cursor.close()
    conn.close()

    if not pages:
        logger.warning("No VDR pages found")
        return []

    logger.info(f"Computing MaxSim for {len(pages)} pages...")

    results = []

    for doc_id, page_num, embedding_vectors_json in pages:
        if isinstance(embedding_vectors_json, str):
            embedding_vectors = json.loads(embedding_vectors_json)
        else:
            embedding_vectors = embedding_vectors_json

        doc_embeddings = np.array(embedding_vectors, dtype=np.float32)

        score = maxsim_score(query_embeddings, doc_embeddings)

        results.append((doc_id, page_num, score))

    results.sort(key=lambda x: x[2], reverse=True)

    if page_level:
        top_results = results[:top_k]
    else:
        doc_scores: Dict[str, float] = {}
        doc_pages: Dict[str, int] = {}

        for doc_id, page_num, score in results:
            if doc_id not in doc_scores or score > doc_scores[doc_id]:
                doc_scores[doc_id] = score
                doc_pages[doc_id] = page_num

        doc_results = [
            (doc_id, doc_pages[doc_id], score)
            for doc_id, score in doc_scores.items()
        ]

        doc_results.sort(key=lambda x: x[2], reverse=True)

        top_results = doc_results[:top_k]

    logger.info(f"MaxSim search complete: {len(top_results)} results")

    return top_results


def generate_query_embeddings(query: str, model) -> np.ndarray:
    """
    Generate query embeddings using VDR model

    Args:
        query: Search query
        model: VDR model dict with model, processor, device

    Returns:
        np.ndarray: Query token embeddings (shape: [num_tokens, embedding_dim])
    """
    import torch

    vdr_model = model["model"]
    processor = model["processor"]
    device = model["device"]

    inputs = processor(text=query, return_tensors="pt", padding=True)
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = vdr_model(**inputs)

        if hasattr(outputs, 'last_hidden_state'):
            embeddings = outputs.last_hidden_state
        elif hasattr(outputs, 'text_embeds'):
            embeddings = outputs.text_embeds
        else:
            embeddings = outputs[0]

        embeddings = embeddings.squeeze(0).cpu().numpy()

    return embeddings


def hybrid_vdr_text_search(
    query: str,
    query_embeddings: np.ndarray,
    document_id: str,
    vdr_weight: float = 0.5,
    text_weight: float = 0.5,
    top_k: int = 10
) -> List[Tuple[str, str, float, int]]:
    """
    Hybrid search combining VDR (page-level) and Text RAG (chunk-level)

    Args:
        query: Search query
        query_embeddings: VDR query embeddings
        document_id: Document ID
        vdr_weight: Weight for VDR results
        text_weight: Weight for Text RAG results
        top_k: Number of results to return

    Returns:
        List[Tuple[str, str, float, int]]: (result_type, content_id, score, page_num)
    """
    logger.info(f"Hybrid VDR+Text search for document {document_id}")

    vdr_results = search_vdr_documents(
        query_embeddings,
        document_id=document_id,
        top_k=top_k * 2,
        page_level=True
    )

    from embedding.hybrid_search import hybrid_search_chunks

    conn = psycopg2.connect(
        host=os.environ.get("RAG_POSTGRES_DB_WRITE_ENDPOINT"),
        database=os.environ.get("RAG_POSTGRES_DB_NAME"),
        user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
        password=os.environ.get("RAG_POSTGRES_DB_SECRET")
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, content, embedding
        FROM chunks
        WHERE document_id = %s
        """,
        (document_id,)
    )

    chunks_data = cursor.fetchall()

    cursor.close()
    conn.close()

    if chunks_data:
        chunks = [content for _, content, _ in chunks_data]
        chunk_ids = [chunk_id for chunk_id, _, _ in chunks_data]
        chunk_embeddings = np.array([emb for _, _, emb in chunks_data])

        query_embedding_dense = query_embeddings.mean(axis=0)

        text_results = hybrid_search_chunks(
            query,
            chunks,
            chunk_embeddings,
            query_embedding_dense,
            top_k=top_k * 2
        )
    else:
        text_results = []

    vdr_scores = {(doc_id, page_num): score for doc_id, page_num, score in vdr_results}
    text_scores = {chunk_ids[idx]: score for idx, score, _ in text_results}

    all_scores = {}

    for (doc_id, page_num), score in vdr_scores.items():
        key = ('vdr', f"{doc_id}:page_{page_num}", page_num)
        all_scores[key] = vdr_weight * score

    for chunk_id, score in text_scores.items():
        key = ('text', chunk_id, 0)
        all_scores[key] = text_weight * score

    sorted_results = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)

    top_results = [
        (result_type, content_id, score, page_num)
        for (result_type, content_id, page_num), score in sorted_results[:top_k]
    ]

    logger.info(f"Hybrid search complete: {len(top_results)} results")

    return top_results
