"""Deletes legacy PostgreSQL embeddings for documents that finished migrating to global2.

An admin-only endpoint (POST /embedding/legacy-cleanup), called by the RAG migration tab when an admin presses the
cleanup button, with that admin's own token. Refuses anything that is not a legacy ``global/`` key, so it can never
touch global2 data, and keeps any document whose global2 twin has no vectors (reported under ``skipped``).
"""
import os
import psycopg2

from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.api.credentials import get_credentials
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from rag.dynamodb_vector_store import DynamoDBVectorStore

setup_validated(rules, get_permission_checker)

from pycommon.logger import getLogger

logger = getLogger("embedding_legacy_cleanup")
MAX_KEYS = 50


def global2_key(legacy_key):
    return "global2/" + legacy_key[len("global/"):]


def without_global2_vectors(src_ids, store=None):
    """Legacy keys whose global2 twin has no vectors yet: these are never deleted."""
    store = store or DynamoDBVectorStore()
    return [key for key in src_ids if not store.exists(global2_key(key))]


def cleanup_legacy(src_ids):
    """Delete the legacy rows of these keys. Returns {"deleted", "errors", "skipped"?}."""
    src_ids = list(src_ids or [])
    if not src_ids or len(src_ids) > MAX_KEYS:
        return {"errors": [f"send between 1 and {MAX_KEYS} keys"]}
    invalid = [key for key in src_ids if not str(key).startswith("global/")]
    if invalid:
        return {"errors": [f"not legacy keys: {invalid[:3]}"]}

    deleted, errors = 0, []
    # Safety: never delete the old copy of a document that has no vectors in its new home.
    try:
        skipped = without_global2_vectors(src_ids)
    except Exception as error:
        logger.error("Could not check the global2 vectors: %s", error)
        return {"deleted": 0, "errors": [f"could not verify global2 vectors: {str(error)[:200]}"]}
    if skipped:
        src_ids = [key for key in src_ids if key not in skipped]
        if not src_ids:
            return {"deleted": 0, "errors": [], "skipped": skipped}
    try:
        with psycopg2.connect(
            host=os.environ["RAG_POSTGRES_DB_WRITE_ENDPOINT"],
            database=os.environ["RAG_POSTGRES_DB_NAME"],
            user=os.environ["RAG_POSTGRES_DB_USERNAME"],
            password=get_credentials(os.environ["RAG_POSTGRES_DB_SECRET"]),
            port=int(os.environ.get("RAG_POSTGRES_DB_PORT", "3306")),
        ) as conn:
            with conn.cursor() as cur:
                for key in src_ids:
                    cur.execute("DELETE FROM embeddings WHERE src = %s", (key,))
                    deleted += cur.rowcount
            conn.commit()
    except Exception as error:
        logger.error("Legacy cleanup failed: %s", error)
        errors.append(str(error)[:300])
    logger.info("Legacy cleanup: %d keys, %d rows deleted, %d errors", len(src_ids), deleted, len(errors))
    return {"deleted": deleted, "errors": errors, **({"skipped": skipped} if skipped else {})}


@validated("legacy_cleanup")
def lambda_handler(event, context, current_user, name, data):
    # Only an administrator may delete embeddings; the token is the caller's own.
    if not verify_user_as_admin(data["access_token"], "Legacy embedding cleanup"):
        return {"success": False, "message": "Unable to authenticate user as admin"}
    logger.warning("LEGACY_CLEANUP admin=%s keys=%s", current_user, data["data"]["src_ids"][:5])
    return {"success": True, "data": cleanup_legacy(data["data"]["src_ids"])}
