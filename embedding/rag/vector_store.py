"""Backend-neutral vector storage boundary for namespace-aware RAG.

The interface is intentionally small. PostgreSQL and the future global2 AWS
adapter can implement the same operations while callers keep permissions,
status, and datasource behavior unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

from pycommon.rag.contracts import EmbeddingNamespace, namespace_for_source, normalize_source_id


class VectorKind(str, Enum):
    CONTENT = "content"
    QA = "qa"


@dataclass(frozen=True)
class VectorRecord:
    key: str
    source_id: str
    vector: Sequence[float]
    kind: VectorKind
    page_id: str
    page_number: int
    content_location: str = ""
    image_location: str = ""
    qa_input_location: str = ""
    metadata: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("vector key is required")
        if not self.source_id:
            raise ValueError("source_id is required")
        if self.page_number < 1:
            raise ValueError("page_number must be >= 1")
        if not self.vector:
            raise ValueError("vector cannot be empty")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "source_id": normalize_source_id(self.source_id),
            "kind": self.kind.value,
            "page_id": self.page_id,
            "page_number": self.page_number,
            "content_location": self.content_location,
            "image_location": self.image_location,
            "qa_input_location": self.qa_input_location,
            **dict(self.metadata),
        }


class VectorStore(Protocol):
    """Operations required by the embedding and retrieval workers."""

    def put(self, records: Iterable[VectorRecord]) -> None:
        ...

    def search(
        self,
        query_vector: Sequence[float],
        source_ids: Sequence[str],
        *,
        kind: VectorKind,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        ...

    def delete(self, source_id: str, *, page_ids: Optional[Sequence[str]] = None) -> None:
        ...

    def exists(self, source_id: str, *, generation_id: str = "") -> bool:
        ...


class UnsupportedGlobal2VectorStore:
    """Fail closed until the approved AWS vector API is configured.

    A global2 source must never silently query or write the legacy PostgreSQL
    store. This explicit placeholder makes that boundary observable while the
    DynamoDB adapter is being built and tested.
    """

    def _error(self) -> RuntimeError:
        return RuntimeError(
            "global2 vector storage is not configured; refusing to fall back to legacy PostgreSQL"
        )

    def put(self, records: Iterable[VectorRecord]) -> None:
        raise self._error()

    def search(self, query_vector, source_ids, *, kind, limit):
        raise self._error()

    def delete(self, source_id: str, *, page_ids=None) -> None:
        raise self._error()

    def exists(self, source_id: str, *, generation_id: str = "") -> bool:
        raise self._error()


def vector_store_for(source_id: str, *, legacy_store: VectorStore, global2_store: Optional[VectorStore] = None) -> VectorStore:
    """Select storage by namespace and fail closed for unconfigured global2."""
    if namespace_for_source(source_id) is EmbeddingNamespace.GLOBAL:
        return legacy_store
    return global2_store or UnsupportedGlobal2VectorStore()


def partition_source_ids(source_ids: Sequence[str]) -> Dict[EmbeddingNamespace, List[str]]:
    """Partition IDs for mixed legacy/global2 retrieval."""
    result = {EmbeddingNamespace.GLOBAL: [], EmbeddingNamespace.GLOBAL2: []}
    for source_id in source_ids:
        normalized = normalize_source_id(source_id)
        result[namespace_for_source(normalized)].append(normalized)
    return result


def legacy_rows(results: Iterable[Mapping[str, Any]]) -> List[tuple]:
    """Shape vector-store hits like legacy PostgreSQL retrieval rows.

    The retrieval API (and the Bedrock KB adapter) return positional rows
    ``(content, src, locations, orig_indexes, char_index, token_count, id, score)``
    that the JavaScript consumer destructures by position, so every backend must
    emit that exact layout. ``score`` is larger-is-more-similar.
    """
    return [
        (
            item.get("content", ""),
            item.get("source_id"),
            item.get("locations") or [],
            item.get("orig_indexes") or [],
            item.get("char_index", 0),
            item.get("token_count", 0),
            item.get("id"),
            item.get("score", 0.0),
        )
        for item in results
    ]
