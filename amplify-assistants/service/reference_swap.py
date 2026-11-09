"""Swap the document ids stored in an assistant's data sources (pure; no AWS).

An assistant that was saved with ``global/<hash>.content.json`` keeps that id forever. When the document has been moved to
the new embeddings (``global2/<hash>.content.json``) the migration swaps the stored id, and swaps it back on rollback.
Only the ``id`` (and a ``key`` equal to it) changes; everything else about a data source is left exactly as it was.
"""

from typing import Any, Dict, List, Mapping, Tuple


def swap_data_source_ids(data_sources: List[Mapping[str, Any]], replacements: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], int]:
    """Return (new data sources, how many were swapped). A data source whose swapped id is already in the list is dropped,
    so the assistant never ends up with the same document twice."""
    swapped = 0
    result: List[Dict[str, Any]] = []
    seen = set()
    for source in data_sources or []:
        item = dict(source)
        old = item.get("id")
        if old in replacements:
            new = replacements[old]
            item["id"] = new
            if item.get("key") == old:
                item["key"] = new
            swapped += 1
        if item.get("id") in seen and swapped:
            continue                      # a duplicate created by the swap
        seen.add(item.get("id"))
        result.append(item)
    return result, swapped
