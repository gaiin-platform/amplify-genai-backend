"""Amazon Nova Multimodal Embeddings request helpers.

This module is provider-only: it does not decide when to embed, how to retry,
or where vectors are stored. The existing embedding worker owns those policies.

Contract (verified against the Nova embeddings schema): a SINGLE_EMBEDDING
request carries exactly ONE of text/image/audio/video. A page that has both
text and an image therefore produces two separate embeddings (two requests),
never one combined request. Index with GENERIC_INDEX regardless of modality;
query a mixed text+image index with GENERIC_RETRIEVAL.
"""

from __future__ import annotations

import base64
import json
import math
from typing import Any, Dict, Mapping, Optional, Sequence


SUPPORTED_DIMENSIONS = frozenset({256, 384, 1024, 3072})
SUPPORTED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
SUPPORTED_DETAIL_LEVELS = frozenset({"STANDARD_IMAGE", "DOCUMENT_IMAGE"})
INDEX_PURPOSE = "GENERIC_INDEX"
MIXED_RETRIEVAL_PURPOSE = "GENERIC_RETRIEVAL"
# Inline request bodies are capped at 25 MB *after* base64 (~33% inflation).
MAX_INLINE_IMAGE_BYTES = 18 * 1024 * 1024


def validate_dimension(dimension: int) -> int:
    if dimension not in SUPPORTED_DIMENSIONS:
        raise ValueError(f"Nova dimension must be one of {sorted(SUPPORTED_DIMENSIONS)}")
    return dimension


def build_text_request(text: str, *, dimension: int = 1024, purpose: str = INDEX_PURPOSE) -> Dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Nova text input must be non-empty")
    validate_dimension(dimension)
    return {
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": purpose,
            "embeddingDimension": dimension,
            "text": {"truncationMode": "END", "value": text.strip()},
        },
    }


def build_image_request(
    image_bytes: bytes,
    *,
    media_type: str = "image/png",
    dimension: int = 1024,
    purpose: str = INDEX_PURPOSE,
    detail_level: str = "DOCUMENT_IMAGE",
) -> Dict[str, Any]:
    if not image_bytes:
        raise ValueError("Nova image input must be non-empty")
    if len(image_bytes) > MAX_INLINE_IMAGE_BYTES:
        raise ValueError("Nova inline image exceeds the 25 MB request limit after base64 encoding")
    if media_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"Unsupported Nova image media type: {media_type}")
    if detail_level not in SUPPORTED_DETAIL_LEVELS:
        raise ValueError(f"Unsupported Nova image detail level: {detail_level}")
    validate_dimension(dimension)
    return {
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": purpose,
            "embeddingDimension": dimension,
            "image": {
                "detailLevel": detail_level,
                "format": media_type.split("/", 1)[1],
                "source": {"bytes": base64.b64encode(image_bytes).decode("ascii")},
            },
        },
    }


def extract_embedding(response_body: Mapping[str, Any], *, expected_dimension: Optional[int] = None) -> Sequence[float]:
    embeddings = response_body.get("embeddings") or []
    if not embeddings or not embeddings[0].get("embedding"):
        raise ValueError("Nova response did not contain an embedding")
    vector = embeddings[0]["embedding"]
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise ValueError(f"Nova returned {len(vector)} dimensions, expected {expected_dimension}")
    if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
        raise ValueError("Nova returned a non-finite (NaN/inf) embedding value")
    return vector


def _token_count(response: Mapping[str, Any], parsed: Mapping[str, Any]) -> int:
    """Nova's body has no token count; Bedrock reports it in a response header."""
    if parsed.get("inputTokenCount"):
        return int(parsed["inputTokenCount"])
    headers = (response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    try:
        return int(headers.get("x-amzn-bedrock-input-token-count", 0))
    except (TypeError, ValueError):
        return 0


def invoke_nova(client: Any, *, model_id: str, request: Mapping[str, Any], expected_dimension: int) -> Dict[str, Any]:
    response = client.invoke_model(modelId=model_id, body=json.dumps(request), accept="application/json", contentType="application/json")
    body = response["body"].read() if hasattr(response["body"], "read") else response["body"]
    parsed = json.loads(body)
    vector = extract_embedding(parsed, expected_dimension=expected_dimension)
    return {"success": True, "data": list(vector), "token_count": _token_count(response, parsed)}
