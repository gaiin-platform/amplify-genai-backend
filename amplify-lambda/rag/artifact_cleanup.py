"""Cleanup of derived global2 artifacts that live beside an extracted-text object.

The existing full-delete path removes the text object, so the page manifest and
rendered page images written during ingestion would otherwise be orphaned
forever. Keys are read from the manifest, so no bucket listing permission is
needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List

logger = logging.getLogger("rag_artifact_cleanup")

DELETE_BATCH = 1000  # S3 DeleteObjects limit


def manifest_key_for(global_key: str) -> str:
    return global_key.removesuffix(".content.json") + ".manifest.json"


def delete_global2_page_artifacts(s3: Any, bucket: str, global_key: str) -> int:
    """Delete the page manifest and page images for a global2 text object.

    Returns the number of objects removed. Never raises: cleanup is best effort
    and must not block the user-facing delete.
    """
    if not str(global_key).startswith("global2/"):
        return 0
    manifest_key = manifest_key_for(global_key)
    try:
        body = s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
        pages = json.loads(body).get("pages", [])
    except Exception as error:
        logger.info("No page manifest to clean up for %s: %s", global_key, error)
        return 0

    keys: List[dict] = []
    for page in pages:
        location = page.get("image_location") or ""
        if location.startswith("s3://"):
            image_bucket, _, image_key = location[len("s3://"):].partition("/")
            if image_bucket == bucket and image_key:  # only ever touch our own bucket
                keys.append({"Key": image_key})
    keys.append({"Key": manifest_key})

    removed = 0
    for start in range(0, len(keys), DELETE_BATCH):
        batch = keys[start:start + DELETE_BATCH]
        try:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            removed += len(batch)
        except Exception as error:
            logger.warning("Could not delete a batch of page artifacts for %s: %s", global_key, error)
    return removed
