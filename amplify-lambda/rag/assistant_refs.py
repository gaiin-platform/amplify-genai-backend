"""Keep the document ids saved inside assistants in step with the migration.

An assistant stores ``global/<hash>.content.json`` for every library document it uses. When a document's references are moved to
the new embeddings the stored id is swapped to ``global2/<hash>.content.json`` (and swapped back on rollback), so no assistant keeps
pointing at an old embedding that is about to be removed. The write itself is done by the assistants service (an admin-only
endpoint, called with the admin's own token); this module only decides which assistants need it.

Only the newest version of each assistant is changed (the one chat uses). Anything that still holds an old id, such as an older
version, an API client or a saved chat, keeps working through the retrieval swap in embedding/migration_hook.py.
"""

import json
import os
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from rag import admin_post, retention

ENDPOINT = "/assistant/migrate_references"
Poster = Callable[[Dict[str, Any]], Mapping[str, Any]]
S3_PREFIX = "s3://"


def plain_id(stored: Any) -> Any:
    """A document id without the ``s3://`` prefix some assistants saved it with (the same document either way)."""
    return stored[len(S3_PREFIX):] if isinstance(stored, str) and stored.startswith(S3_PREFIX) else stored


def same_form(stored: str, new: str) -> str:
    """The new id written the way the assistant stored the old one, so an ``s3://`` id stays an ``s3://`` id."""
    return S3_PREFIX + new if stored.startswith(S3_PREFIX) else new


def latest_versions(assistants_table: Any) -> Dict[str, Mapping[str, Any]]:
    """Public assistant id -> its newest saved version."""
    newest: Dict[str, Mapping[str, Any]] = {}
    for item in retention._scan_all(assistants_table):
        public_id = item.get("assistantId") or item.get("id")
        if public_id and (public_id not in newest or float(item.get("version") or 0) > float(newest[public_id].get("version") or 0)):
            newest[public_id] = item
    return newest


def affected(assistants_table: Any, mapping: Mapping[str, str]) -> Dict[str, Dict[str, str]]:
    """Assistant -> {stored id: id to store instead}, for the newest version of every assistant that holds a mapped id."""
    found: Dict[str, Dict[str, str]] = {}
    for public_id, item in latest_versions(assistants_table).items():
        wanted = {
            source["id"]: same_form(source["id"], mapping[plain_id(source["id"])])
            for source in item.get("dataSources") or []
            if isinstance(source, Mapping) and plain_id(source.get("id")) in mapping
        }
        if wanted:
            found[public_id] = wanted
    return found


def usage(assistants_table: Any, pairs: Mapping[str, str]) -> Dict[str, Dict[str, Any]]:
    """For each document (old id -> new id): which assistants (newest versions) store the old id and which store the new one.

    One pass over the assistants table for all documents. Names are for display only.
    """
    result = {old: {"onOld": [], "onNew": []} for old in pairs}
    by_new = {new: old for old, new in pairs.items()}
    for public_id, item in latest_versions(assistants_table).items():
        stored = {plain_id(source.get("id")) for source in item.get("dataSources") or [] if isinstance(source, Mapping)}
        label = {"assistantId": public_id, "name": item.get("name") or public_id}
        for old in stored & set(pairs):
            result[old]["onOld"].append(label)
        for new in stored & set(by_new):
            result[by_new[new]]["onNew"].append(label)
    return result


def apply(poster: Optional[Poster], assistants_table: Any, mapping: Mapping[str, str]) -> Dict[str, Any]:
    """Swap the ids in every assistant that holds one. One assistant failing never stops the others."""
    result: Dict[str, Any] = {"assistants": 0, "updated": [], "failed": []}
    if not mapping or poster is None or assistants_table is None:
        return result
    for public_id, replacements in affected(assistants_table, mapping).items():
        result["assistants"] += 1
        try:
            answer = poster({"assistantId": public_id, "replacements": replacements})
            if answer.get("data", {}).get("changed"):
                result["updated"].append(public_id)
        except Exception as error:
            result["failed"].append({"assistantId": public_id, "error": str(error)[:300]})
    return result


def api_poster(access_token: str, base_url: Optional[str] = None, session: Any = None) -> Poster:
    """Call the assistants endpoint with the admin's token. Raises with the server's message when it refuses."""
    def post(body: Dict[str, Any]) -> Mapping[str, Any]:
        return admin_post.post_as_admin(ENDPOINT, body, access_token, base_url, session)

    return post
