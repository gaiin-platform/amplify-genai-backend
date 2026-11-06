"""Reference rules for deleting an uploaded file's shared content.

Uploads of identical bytes share one content object (``global/<hash>`` or
``global2/<hash>``). The hash-files table holds two kinds of rows pointing at it:

  * an *upload pointer*, one per uploaded file: ``id`` is the user's file key and
    the row carries a ``hash`` attribute;
  * the *content row*: ``id`` is the content hash itself, no ``hash`` attribute.

Deleting one upload must remove only that upload's pointer. The previous logic
removed every pointer the same user had to the content, so deleting a chat copy
of a document also broke the same user's assistant copy of identical bytes.
Shared content may be removed only when no other upload still points at it.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional

CONTENT_KEY = re.compile(r"^(global2?)/([0-9a-f]{64})\.content\.json$")


def is_upload_pointer(row: Mapping) -> bool:
    return "hash" in row


def pointer_owner(row: Mapping) -> str:
    return row.get("originalCreator") or str(row.get("id", "")).split("/")[0]


def pointer_ids_to_remove(rows: Iterable[Mapping], key: str) -> List[str]:
    """Only the pointer belonging to this upload; never a sibling's or the content row."""
    return [row["id"] for row in rows if row.get("id") == key and is_upload_pointer(row)]


def other_references(rows: Iterable[Mapping], key: str) -> List[str]:
    """Upload pointers, besides this one, that still resolve to the same content."""
    return [row["id"] for row in rows if is_upload_pointer(row) and row.get("id") != key]


def user_still_references(rows: Iterable[Mapping], key: str, user: str) -> bool:
    """True when this user has another upload of the same content.

    Their access row on the content must then be kept, or the remaining upload
    would stop resolving.
    """
    return any(
        is_upload_pointer(row) and row.get("id") != key and pointer_owner(row) == user
        for row in rows
    )


def is_content_key(key: str) -> bool:
    """True for ``global/<sha>.content.json`` and ``global2/<sha>.content.json`` (not an upload key)."""
    return bool(CONTENT_KEY.match(str(key)))


def content_key_candidates(key: str) -> List[str]:
    """The key plus its twin in the other namespace (same hash).

    An assistant keeps the content key it was saved with. After a document moves from ``global/`` to
    ``global2/`` its uploads point at the new key, so the old one finds none.
    """
    match = CONTENT_KEY.match(str(key))
    if not match:
        return [key]
    other = "global2" if match.group(1) == "global" else "global"
    return [key, f"{other}/{match.group(2)}.content.json"]


def pick_upload_key(rows: Iterable[Mapping], owner: str) -> Optional[str]:
    """The upload to reprocess for a content key: the owner's own (id prefix), else any other, in a stable order."""
    ids = sorted(row["id"] for row in rows if is_upload_pointer(row) and row.get("id"))
    own = [i for i in ids if i.split("/")[0] == owner]
    return (own or ids or [None])[0]
