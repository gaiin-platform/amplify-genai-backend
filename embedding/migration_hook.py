"""Queue a legacy document for migration the first time a user retrieves from it.

Only active while an admin has turned on "migrate on first use" in the RAG Migration
tab. Failure-safe by design: retrieval must never depend on this.
"""
import time

LEGACY_PREFIX = "global/"
NEW_PREFIX = "global2/"
CONFIG_TTL_SECONDS = 60
SERVED_FROM_GLOBAL2 = ("cutover", "cleaned")     # migration states in which a legacy key is answered from global2

_config_cache = {"at": 0.0, "enabled": False}
_queued = set()          # per warm container: avoids repeating writes for the same document
_alias_cache = {}        # legacy key -> (checked at, global2 key or None)
_never_embedded_cache = {}


def _enabled(table, now):
    if now - _config_cache["at"] > CONFIG_TTL_SECONDS:
        item = table.get_item(Key={"id": "config"}).get("Item") or {}
        _config_cache.update(at=now, enabled=bool(item.get("migrateOnAccess")))
    return _config_cache["enabled"]


def queue_for_migration(source_ids, table, now=time.time):
    """Add ``doc#<key>`` rows (status planned, trigger access); existing rows are never changed."""
    if table is None:
        return 0
    try:
        moment = now()
        if not _enabled(table, moment):
            return 0
        added = 0
        for source_id in source_ids or []:
            if not str(source_id).startswith(LEGACY_PREFIX) or source_id in _queued:
                continue
            _queued.add(source_id)
            try:
                table.put_item(
                    Item={"id": f"doc#{source_id}", "contentKey": source_id, "status": "planned", "trigger": "access",
                          "attempts": 0, "createdAt": int(moment), "updatedAt": int(moment)},
                    ConditionExpression="attribute_not_exists(id)",
                )
                added += 1
            except Exception as error:      # already queued (conditional failure) or throttled
                if "ConditionalCheckFailed" not in str(error):
                    _queued.discard(source_id)
        return added
    except Exception:
        return 0


def _plain(source_id):
    text = str(source_id)
    return text.split("://", 1)[1] if "://" in text else text


def global2_twin(source_id):
    """The ``global2/`` key with the same hash as a legacy ``global/`` id, else None."""
    key = _plain(source_id)
    return NEW_PREFIX + key[len(LEGACY_PREFIX):] if key.startswith(LEGACY_PREFIX) else None


def _alias(source_ids, resolve, cache, now):
    """Swap each legacy id for ``resolve(key)`` when that gives a global2 key; see alias_sources for the result."""
    original, result, seen = {}, [], set()
    moment = now()
    for source_id in source_ids or []:
        target = None
        key = _plain(source_id)
        if key.startswith(LEGACY_PREFIX):
            cached = cache.get(key)
            if cached is None or moment - cached[0] > CONFIG_TTL_SECONDS:
                try:
                    target = resolve(key)
                    cache[key] = (moment, target)
                except Exception:
                    target = None
            else:
                target = cached[1]
        chosen = target or source_id
        if target:
            original.setdefault(target, source_id)
        if chosen not in seen:
            seen.add(chosen)
            result.append(chosen)
    return result, original


def alias_sources(source_ids, table, now=time.time):
    """Answer a migrated legacy document from its global2 copy.

    Callers (older assistants, API clients, saved integrations) still send ``global/<hash>...``. Once the
    migration has switched that document over (status cutover/cleaned) the same hash exists as ``global2/``,
    so the id is swapped here. Call this AFTER the access checks, which keep using the id the caller sent.

    Returns (ids, original): ``ids`` is the list with swaps applied and duplicates removed, ``original`` maps
    each swapped-in global2 id back to the id the caller sent, so results can be reported under that id.
    Failure-safe: any problem leaves the ids unchanged, so retrieval never depends on this.
    """
    if table is None:
        return _alias(source_ids, lambda key: None, {}, now)

    def resolve(key):
        doc = table.get_item(Key={"id": f"doc#{key}"}).get("Item") or {}
        new_key = doc.get("newKey")
        return new_key if doc.get("status") in SERVED_FROM_GLOBAL2 and new_key else None

    return _alias(source_ids, resolve, _alias_cache, now)


def alias_never_embedded(source_ids, twin_ready, now=time.time):
    """Answer a legacy document that was never embedded from the global2 copy built for it later.

    A library file uploaded with RAG off before the new pipeline has a ``global/`` id but no legacy embeddings; when it
    is attached to an assistant its embeddings are built as ``global2/`` (never in Postgres). ``twin_ready(legacy_key,
    twin_key)`` says whether that copy exists and the legacy one has no embeddings. Same result shape and the same
    after-the-access-checks rule as alias_sources.
    """
    return _alias(source_ids, lambda key: global2_twin(key) if twin_ready(key, global2_twin(key)) else None,
                  _never_embedded_cache, now)
