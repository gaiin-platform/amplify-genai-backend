"""Asynchronous Textract job contracts for large/scanned documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class TextractJob:
    source_bucket: str
    source_key: str
    source_version: str = ""
    feature: str = "TEXT_DETECTION"
    job_id: str = ""
    status: str = "submitted"

    @property
    def idempotency_key(self) -> str:
        raw = json.dumps({"bucket": self.source_bucket, "key": self.source_key, "version": self.source_version, "feature": self.feature}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_item(self) -> Dict[str, Any]:
        return {
            "id": self.idempotency_key,
            "sourceBucket": self.source_bucket,
            "sourceKey": self.source_key,
            "sourceVersion": self.source_version,
            "feature": self.feature,
            "jobId": self.job_id,
            "status": self.status,
        }


def build_start_request(job: TextractJob) -> Dict[str, Any]:
    """Build StartDocument{TextDetection,Analysis} arguments without polling.

    The asynchronous APIs take ``DocumentLocation`` (the synchronous
    DetectDocumentText/AnalyzeDocument calls take ``Document``). The client
    request token makes SQS redeliveries return the same job instead of
    starting (and paying for) a second one.
    """
    request = {
        "DocumentLocation": {"S3Object": {"Bucket": job.source_bucket, "Name": job.source_key}},
        "ClientRequestToken": job.idempotency_key,
    }
    if job.feature == "ANALYSIS":
        request["FeatureTypes"] = ["TABLES", "FORMS"]
    return request


def normalize_result_block(block: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize a Textract block while preserving page/bounding-box provenance."""
    geometry = block.get("Geometry", {})
    return {
        "id": block.get("Id"),
        "block_type": block.get("BlockType"),
        "text": block.get("Text", ""),
        "confidence": block.get("Confidence"),
        "page_number": block.get("Page", 1),
        "geometry": geometry.get("BoundingBox", {}),
        "relationships": block.get("Relationships", []),
    }
