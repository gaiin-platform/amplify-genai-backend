"""Deterministic page artifacts for the global2 RAG pipeline.

This module is intentionally storage-agnostic. It builds page manifests and
page-local chunks that can be persisted in S3 or sent through SQS later. It
does not call Textract, Nova, a vision model, or a vector database.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from pycommon.rag.contracts import PageInput, build_page_input, generation_fingerprint, page_id


PAGE_MANIFEST_VERSION = "global2-page-manifest-v1"


@dataclass(frozen=True)
class PageArtifact:
    """One page's durable text/image references and provenance."""

    source_id: str
    page_number: int
    text: str = ""
    text_location: str = ""
    image_location: str = ""
    extraction_method: str = ""
    source_version: str = ""
    generation_id: str = ""
    locations: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    content_hash: str = ""

    @property
    def id(self) -> str:
        return page_id(self.source_id, self.page_number, self.generation_id)

    def to_page_input(self) -> PageInput:
        return build_page_input(
            self.source_id,
            self.page_number,
            text=self.text,
            text_location=self.text_location,
            image_location=self.image_location,
            extraction_method=self.extraction_method,
            generation_id=self.generation_id,
            locations=self.locations,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["locations"] = [dict(location) for location in self.locations]
        data["page_id"] = self.id
        return data


@dataclass(frozen=True)
class PageManifest:
    """Replayable document preparation output."""

    source_id: str
    content_hash: str
    source_version: str
    generation_id: str
    extraction_mode: str
    pages: Sequence[PageArtifact]
    manifest_version: str = PAGE_MANIFEST_VERSION
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", manifest_fingerprint(self))

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifestVersion": self.manifest_version,
            "sourceId": self.source_id,
            "contentHash": self.content_hash,
            "sourceVersion": self.source_version,
            "generationId": self.generation_id,
            "extractionMode": self.extraction_mode,
            "pageCount": self.page_count,
            "fingerprint": self.fingerprint,
            "pages": [page.to_dict() for page in self.pages],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def page_artifact_key(source_id: str, page_number: int, *, extension: str = "json") -> str:
    """Return a stable page artifact key below the source's global2 prefix."""
    normalized = source_id.removeprefix("s3://")
    if not normalized.startswith("global2/"):
        raise ValueError("page artifacts require a global2 source ID")
    if page_number < 1:
        raise ValueError("page_number must be >= 1")
    clean_extension = extension.lstrip(".")
    stem = normalized.removeprefix("global2/").removesuffix(".content.json")
    return f"global2/{stem}/pages/{page_number}.{clean_extension}"


def build_page_artifact(
    source_id: str,
    page_number: int,
    *,
    text: str = "",
    text_location: str = "",
    image_location: str = "",
    extraction_method: str = "",
    source_version: str = "",
    generation_id: str = "",
    locations: Optional[Sequence[Mapping[str, Any]]] = None,
    image_hash: str = "",
) -> PageArtifact:
    """Construct a page record with a content hash for replay/reuse checks."""
    if not text and not image_location:
        raise ValueError("a page must contain text or an image location")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
    return PageArtifact(
        source_id=source_id.removeprefix("s3://"),
        page_number=page_number,
        text=text,
        text_location=text_location,
        image_location=image_location,
        extraction_method=extraction_method,
        source_version=source_version,
        generation_id=generation_id,
        locations=tuple(dict(location) for location in (locations or ())),
        content_hash=image_hash or content_hash,
    )


def build_page_manifest(
    source_id: str,
    content_hash: str,
    *,
    source_version: str = "",
    extraction_mode: str = "",
    pages: Optional[Sequence[PageArtifact]] = None,
    model_id: str = "",
    embedding_dimension: Optional[int] = None,
    embedding_purpose: str = "",
) -> PageManifest:
    """Build an ordered manifest and generation ID for a document."""
    generation_id = generation_fingerprint(
        content_hash=content_hash,
        source_version=source_version,
        model_id=model_id,
        embedding_dimension=embedding_dimension,
        embedding_purpose=embedding_purpose,
        representation="page",
    )
    normalized_pages_data = []
    for page in (pages or ()):
        page_data = asdict(page)
        page_data["source_id"] = page.source_id.removeprefix("s3://")
        page_data["generation_id"] = generation_id
        normalized_pages_data.append(PageArtifact(**page_data))
    normalized_pages = tuple(normalized_pages_data)
    return PageManifest(
        source_id=source_id.removeprefix("s3://"),
        content_hash=content_hash,
        source_version=source_version,
        generation_id=generation_id,
        extraction_mode=extraction_mode,
        pages=normalized_pages,
    )


def manifest_fingerprint(manifest: PageManifest) -> str:
    """Hash structural page metadata, excluding the fingerprint itself."""
    payload = {
        "manifestVersion": manifest.manifest_version,
        "sourceId": manifest.source_id,
        "contentHash": manifest.content_hash,
        "sourceVersion": manifest.source_version,
        "generationId": manifest.generation_id,
        "extractionMode": manifest.extraction_mode,
        "pages": [page.to_dict() for page in manifest.pages],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def page_chunks(page: PageArtifact, *, max_chars: int = 26000) -> List[Dict[str, Any]]:
    """Create deterministic page-local chunks while preserving page provenance."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if not page.text:
        return [{
            "id": f"{page.id}:chunk:1:content",
            "content": "",
            "page_number": page.page_number,
            "page_id": page.id,
            "locations": [dict(location) for location in page.locations],
            "image_location": page.image_location,
            "canSplit": False,
        }]

    chunks = []
    for index in range(0, len(page.text), max_chars):
        content = page.text[index:index + max_chars]
        chunks.append({
            "id": f"{page.id}:chunk:{len(chunks) + 1}:content",
            "content": content,
            "page_number": page.page_number,
            "page_id": page.id,
            "char_index": index,
            "locations": [dict(location) for location in page.locations],
            "image_location": page.image_location,
            "canSplit": len(content) < max_chars,
        })
    return chunks
