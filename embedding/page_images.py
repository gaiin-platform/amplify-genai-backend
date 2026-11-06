"""Page-image vectors for global2 documents.

Nova accepts one modality per request, so a PDF page produces a text vector
(from the chunk) and, separately, an image vector (from the rendered page). This
module owns the image half. Dependencies (S3, the vector store, the embedder)
are injected so the logic is testable without AWS.

Image vectors are a bonus retrieval signal: every failure here is logged and
swallowed so it can never fail or retry the text/QA embedding of a chunk.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional

from rag.vector_store import VectorKind, VectorRecord

logger = logging.getLogger("page_images")

MAX_IMAGE_RECORD_CONTENT_CHARS = 26000
MANIFEST_CACHE_SIZE = 4


def image_vector_key(source_id: str, page_number: int) -> str:
    """One image vector per page, shared by every chunk that touches the page."""
    return f"{source_id}:page:{page_number}:image"


def manifest_key_for(source_id: str) -> str:
    return source_id.removeprefix("s3://").removesuffix(".content.json") + ".manifest.json"


def load_page_manifest(
    source_id: str,
    *,
    s3_client: Any,
    bucket: str,
    cache: Optional[MutableMapping[str, Dict[int, Mapping[str, Any]]]] = None,
) -> Dict[int, Mapping[str, Any]]:
    """Return {page_number: page dict} from ``global2/<hash>.manifest.json`` or {}.

    A missing manifest is normal (non-paged documents), not an error.
    """
    if cache is not None and source_id in cache:
        return cache[source_id]
    pages: Dict[int, Mapping[str, Any]] = {}
    try:
        body = s3_client.get_object(Bucket=bucket, Key=manifest_key_for(source_id))["Body"].read()
        for page in json.loads(body).get("pages", []):
            if page.get("page_number"):
                pages[int(page["page_number"])] = page
    except Exception as error:
        logger.info("[PAGE_IMAGES] No usable page manifest for %s: %s", source_id, error)
    if cache is not None:
        if len(cache) >= MANIFEST_CACHE_SIZE:
            cache.pop(next(iter(cache)))
        cache[source_id] = pages
    return pages


def pages_in_locations(locations: Optional[Iterable[Any]]) -> list:
    """Sorted distinct page numbers referenced by a chunk's location dicts."""
    return sorted({
        int(location["page_number"])
        for location in (locations or [])
        if isinstance(location, Mapping) and str(location.get("page_number", "")).isdigit()
    })


def embed_page_images(
    source_id: str,
    child_chunk: Any,
    locations: Optional[Iterable[Any]],
    *,
    manifest: Mapping[int, Mapping[str, Any]],
    store: Any,
    s3_client: Any,
    embed: Callable[..., Mapping[str, Any]],
) -> int:
    """Embed each referenced page's image once; return how many were stored.

    ``embed(image_bytes=..., image_media_type=...)`` must return the usual
    ``{"success", "data", "token_count"}`` shape.
    """
    stored = 0
    for page_number in pages_in_locations(locations):
        page = manifest.get(page_number) or {}
        image_location = page.get("image_location")
        if not image_location:
            continue
        key = image_vector_key(source_id, page_number)
        try:
            if store.has_key(source_id, key):
                continue
            image_bucket, image_key = image_location.removeprefix("s3://").split("/", 1)
            image_object = s3_client.get_object(Bucket=image_bucket, Key=image_key)
            result = embed(
                image_bytes=image_object["Body"].read(),
                image_media_type=image_object.get("ContentType") or "image/png",
            )
            if not result["success"]:
                logger.warning("[PAGE_IMAGES] Image embedding failed for %s page %s: %s", source_id, page_number, result.get("error"))
                continue
            store.put([VectorRecord(
                key=key,
                source_id=source_id,
                vector=result["data"],
                kind=VectorKind.CONTENT,
                page_id=f"{source_id}:page:{page_number}",
                page_number=page_number,
                content_location=source_id,
                image_location=image_location,
                metadata={
                    "modality": "image",
                    "child_chunk": child_chunk,
                    "embedding_index": 0,
                    # A hit on the page image returns that page's text as context.
                    "content": (page.get("text") or "")[:MAX_IMAGE_RECORD_CONTENT_CHARS],
                    "locations": [{"page_number": page_number}],
                    "orig_indexes": [],
                    "char_index": 0,
                    "token_count": result.get("token_count", 0),
                },
            )])
            stored += 1
        except Exception as error:
            logger.warning("[PAGE_IMAGES] Skipping page image %s page %s: %s", source_id, page_number, error)
    return stored
