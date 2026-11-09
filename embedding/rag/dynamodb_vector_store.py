"""Native DynamoDB vector-index adapter for global2 embeddings.

The adapter uses DynamoDB's SearchVectors API. It keeps the full source text
and page images in S3; the vector index stores compact projected pointers and
provenance. Authorization remains outside this adapter.

Score semantics (per the SearchVectors API): for COSINE and EUCLIDEAN the
service returns *distances* (smaller is more similar); for DOT_PRODUCT larger
is more similar. ``search`` normalizes every result to a ``score`` where larger
is always more similar (the convention the legacy PostgreSQL path and the
JavaScript consumer use) and keeps the raw service value as ``distance``.
"""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from rag.vector_store import VectorKind, VectorRecord
from pycommon.rag.contracts import EmbeddingNamespace, namespace_for_source, normalize_source_id



def default_tenant_id() -> str:
    """The tenant stored on every vector row and used to filter every search: ``<deployment name>_<stage>``, e.g. ``v6_dev``.

    Read from the stack's own SERVICE_NAME (``amplify-<deployment name>-embedding`` in dev, ``<deployment name>-embedding``
    where the deployment name already carries the prefix, e.g. ``vu-amplify-embedding``) and STAGE. It fails loudly instead of
    guessing: a different value would write rows that no search could find. Never change it once documents exist.
    """
    match = re.fullmatch(r"(?:amplify-)?(.+)-embedding", os.environ.get("SERVICE_NAME", ""))
    stage = os.environ.get("STAGE", "")
    if not match or not stage:
        raise ValueError("SERVICE_NAME (<name>-embedding) and STAGE are required to name the vector tenant")
    return f"{match.group(1)}_{stage}"

# Name of the vector table's search index. Fixed: embedding/serverless.yml creates the index under this same name.
VECTOR_INDEX_NAME = "global2-vector-index"
SUPPORTED_DISTANCE_FUNCTIONS = frozenset({"COSINE", "EUCLIDEAN", "DOT_PRODUCT"})
MAX_PARALLEL_SOURCE_SEARCHES = 8

# Attributes returned from SearchVectors. Aliased so no attribute name can
# collide with a DynamoDB reserved word.
_PROJECTED_ATTRIBUTES = (
    "id", "source_id", "kind", "page_id", "page_number", "content_location",
    "image_location", "qa_input_location", "content", "locations",
    "orig_indexes", "char_index", "token_count", "modality",
)


def _to_dynamodb_value(value: Any) -> Any:
    """Make a Python value serializable by boto3's TypeSerializer.

    boto3 rejects ``float`` (extractor locations can carry coordinates), so
    floats become ``Decimal``. Tuples become lists and mapping keys become str.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float cannot be stored in DynamoDB")
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _to_dynamodb_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamodb_value(inner) for inner in value]
    return value


def _to_native(value: Any) -> Any:
    """Convert DynamoDB ``Decimal`` values back to JSON-serializable numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _to_native(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(inner) for inner in value]
    if isinstance(value, (set, frozenset)):
        return [_to_native(inner) for inner in value]
    return value


class DynamoDBVectorStore:
    """Write/search/delete global2 vectors through native DynamoDB indexes."""

    def __init__(
        self,
        *,
        table_name: Optional[str] = None,
        index_name: Optional[str] = None,
        client: Any = None,
        dimension: Optional[int] = None,
        tenant_id: Optional[str] = None,
        distance_function: Optional[str] = None,
    ) -> None:
        self.table_name = table_name or os.environ.get("GLOBAL2_VECTOR_TABLE")
        if self.table_name and namespace_for_source("global2/example.content.json") is not EmbeddingNamespace.GLOBAL2:
            raise ValueError("global2 namespace routing is unavailable")
        self.index_name = index_name or VECTOR_INDEX_NAME
        self.dimension = dimension or int(os.environ.get("GLOBAL2_EMBEDDING_DIM", "1024"))
        self.tenant_id = tenant_id or default_tenant_id()
        self.distance_function = (distance_function or os.environ.get("GLOBAL2_DISTANCE_FUNCTION") or "COSINE").upper()
        if self.distance_function not in SUPPORTED_DISTANCE_FUNCTIONS:
            raise ValueError(f"distance function must be one of {sorted(SUPPORTED_DISTANCE_FUNCTIONS)}")
        if not self.table_name:
            raise ValueError("GLOBAL2_VECTOR_TABLE is required")
        if self.dimension < 1 or self.dimension > 4096:
            raise ValueError("global2 vector dimension must be between 1 and 4096")
        self.client = client or boto3.client("dynamodb")
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def _validate_vector(self, vector: Sequence[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError(f"expected {self.dimension}-dimension vector, got {len(vector)}")
        if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
            raise ValueError("vector must contain only finite numeric values")

    def _attribute_value(self, value: Any) -> Dict[str, Any]:
        return self._serializer.serialize(value)

    def _item(self, record: VectorRecord) -> Dict[str, Dict[str, Any]]:
        item: Dict[str, Any] = {
            "id": record.key,
            "tenant_id": str(record.metadata.get("tenant_id", self.tenant_id)),
            "source_id": normalize_source_id(record.source_id),
            "kind": record.kind.value,
            "page_id": record.page_id,
            "page_number": record.page_number,
            "content_location": record.content_location,
            "image_location": record.image_location,
            "qa_input_location": record.qa_input_location,
            "vector": [Decimal(str(value)) for value in record.vector],
            **dict(record.metadata),
        }
        item = {key: _to_dynamodb_value(value) for key, value in item.items()}
        return {key: self._attribute_value(value) for key, value in item.items() if value not in (None, "")}

    def put(self, records: Iterable[VectorRecord]) -> None:
        records = list(records)
        for record in records:
            self._validate_vector(record.vector)
        for record in records:
            self.client.put_item(TableName=self.table_name, Item=self._item(record))

    def _similarity(self, raw_score: Any) -> float:
        """Normalize a service score so larger always means more similar."""
        score = float(raw_score if raw_score is not None else 0.0)
        if self.distance_function == "COSINE":
            return 1.0 - score  # service range: 0 (identical) .. 2 (opposite)
        if self.distance_function == "EUCLIDEAN":
            return 1.0 / (1.0 + score)
        return score  # DOT_PRODUCT: already larger-is-better

    def _search_one_source(self, query_vector: Sequence[float], source_id: str, kind: VectorKind, limit: int) -> List[Dict[str, Any]]:
        names = {"#tenant": "tenant_id", "#source": "source_id", "#kind": "kind"}
        projection = []
        for position, attribute in enumerate(_PROJECTED_ATTRIBUTES):
            alias = f"#p{position}"
            names[alias] = attribute
            projection.append(alias)
        response = self.client.search_vectors(
            TableName=self.table_name,
            IndexName=self.index_name,
            SearchVector=[self._attribute_value(Decimal(str(value))) for value in query_vector],
            TopK=limit,
            SearchConditionExpression="#tenant = :tenant AND #source = :source AND #kind = :kind",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={
                ":tenant": self._attribute_value(self.tenant_id),
                ":source": self._attribute_value(source_id),
                ":kind": self._attribute_value(kind.value),
            },
            ProjectionExpression=",".join(projection),
        )
        results: List[Dict[str, Any]] = []
        for result in response.get("SearchResults", []):
            item = {
                key: _to_native(self._deserializer.deserialize(value))
                for key, value in result.get("Item", {}).items()
            }
            item["distance"] = result.get("Score")
            item["score"] = self._similarity(result.get("Score"))
            results.append(item)
        return results

    def search(
        self,
        query_vector: Sequence[float],
        source_ids: Sequence[str],
        *,
        kind: VectorKind,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        self._validate_vector(query_vector)
        if limit < 1 or limit > 100:
            raise ValueError("DynamoDB SearchVectors limit must be between 1 and 100")
        normalized_sources = list(dict.fromkeys(normalize_source_id(source_id) for source_id in source_ids))
        if not normalized_sources:
            return []

        # SearchVectors supports equality filters only. Query each source so
        # filtering is explicit and cannot accidentally broaden access. Sources
        # are independent, so they are searched concurrently.
        workers = min(MAX_PARALLEL_SOURCE_SEARCHES, len(normalized_sources))
        if workers == 1:
            per_source = [self._search_one_source(query_vector, normalized_sources[0], kind, limit)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                per_source = list(pool.map(
                    lambda source_id: self._search_one_source(query_vector, source_id, kind, limit),
                    normalized_sources,
                ))
        merged = [item for results in per_source for item in results]
        merged.sort(key=lambda item: item["score"], reverse=True)
        return merged[:limit]

    def delete(self, source_id: str, *, page_ids: Optional[Sequence[str]] = None) -> int:
        """Delete the vectors stored for a source (optionally only some pages); returns how many were removed."""
        normalized = normalize_source_id(source_id)
        removed = 0
        for key in self._find_keys(normalized, page_ids=page_ids):
            self.client.delete_item(
                TableName=self.table_name,
                Key={"source_id": self._attribute_value(normalized), "id": self._attribute_value(key)},
            )
            removed += 1
        return removed

    def exists(self, source_id: str, *, generation_id: str = "") -> bool:
        return bool(self._find_keys(normalize_source_id(source_id), generation_id=generation_id))

    def has_key(self, source_id: str, key: str) -> bool:
        """Cheap point read used to skip re-embedding an already stored vector."""
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"source_id": self._attribute_value(normalize_source_id(source_id)), "id": self._attribute_value(key)},
            ProjectionExpression="#id",
            ExpressionAttributeNames={"#id": "id"},
            ConsistentRead=True,
        )
        return bool(response.get("Item"))

    def _find_keys(
        self,
        source_id: str,
        *,
        page_ids: Optional[Sequence[str]] = None,
        generation_id: str = "",
    ) -> List[str]:
        """All vector ids stored for a source.

        The table is keyed (source_id, id), so this is a Query over one partition:
        cost scales with the document, not the whole table. That matters for bulk
        deletes (retention sweeps).
        """
        keys: List[str] = []
        allowed_pages = set(page_ids or ())
        query_kwargs: Dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": "#source = :source",
            "ProjectionExpression": "#id, #page, #generation",
            "ExpressionAttributeNames": {
                "#id": "id", "#source": "source_id", "#page": "page_id", "#generation": "generation_id",
            },
            "ExpressionAttributeValues": {":source": self._attribute_value(source_id)},
        }
        while True:
            response = self.client.query(**query_kwargs)
            for raw_item in response.get("Items", []):
                item = {key: self._deserializer.deserialize(value) for key, value in raw_item.items()}
                if allowed_pages and item.get("page_id") not in allowed_pages:
                    continue
                if generation_id and item.get("generation_id") != generation_id:
                    continue
                if item.get("id"):
                    keys.append(item["id"])
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return keys
            query_kwargs["ExclusiveStartKey"] = last_key
