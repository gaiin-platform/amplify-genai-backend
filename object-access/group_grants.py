"""
Group-derived read grants on object-access records.

WHY THIS EXISTS
Files attached to a *group* assistant carry a permission record for the **group** principal only
(`groups.update_group_ds_perms`). A group member who copies that assistant into a private/shared one
must be able to attach those files, and the copy's owner must then be able to chat with them. Both
steps read the **per-user** record directly:

  * `can_access_objects` (assistant create, file download, layered routing), and
  * chat-time retrieval (`embedding-dual-retrieval.classify_src_ids_by_access`), which queries
    `(object_id, principal_id=<user>)` straight from DynamoDB — it cannot be made to derive group
    membership without changing the embedding service, and `create_assistant`'s own
    `update_object_permissions` does not mint a record for the creator on files they don't already own.

So the grant has to be a real, persisted per-user record. To keep it from outliving the membership that
justified it, every such record is TAGGED with the group it came from (`granted_via_group`) and:

  1. re-verified on every access check (`grant_is_valid`) — a tagged record whose user is no longer a
     member is deleted and treated as absent, so `can_access_objects` and `simulate_access_to_objects`
     stop honouring it at once;
  2. deleted eagerly when the member is removed or the group is deleted (`revoke_group_grants`), which is
     what stops the chat-time retrieval path that reads the table directly.

Only *read* is ever granted, and only on objects that already have a read-capable record for a group the
user belongs to. Never overwrites an existing record.

Table/group-table handles are passed in, so the logic is testable without AWS.
No decorators, no pycommon imports required.
"""

import logging

try:  # inside the lambda the shared logger is available
    from pycommon.logger import getLogger

    logger = getLogger("group_grants")
except Exception:  # pragma: no cover - local/unit-test runs
    logger = logging.getLogger("group_grants")

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

#: Attribute stamped on a record that exists only because of group membership.
GRANT_SOURCE_ATTR = "granted_via_group"


def is_group_member(group_item, user):
    """Any real membership counts for the grant: a member of any role, or a listed system user."""
    if not group_item or not user:
        return False
    return user in (group_item.get("members") or {}) or user in (group_item.get("systemUsers") or [])


def _record_allows_read(record):
    """Same meaning as `object_access.is_sufficient_privilege(..., 'read')`."""
    level = record.get("permission_level")
    if level in ("owner", "write", "read"):
        return True
    if level == "none":
        return False
    return record.get("policy") == "public"


def _query_all(table, **kwargs):
    """Every item of a query, across pages."""
    items = []
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def grant_read_via_group_membership(table, groups_table, object_id, access_type, user):
    """
    Return a (new or already existing) record letting `user` read `object_id` because they belong to a group
    that has a read-capable record on it; otherwise None. Read only: anything stronger needs a direct record.
    """
    if access_type != "read":
        return None

    for record in _query_all(table, KeyConditionExpression=Key("object_id").eq(object_id)):
        if record.get("principal_type") != "group" or not _record_allows_read(record):
            continue
        group_id = record["principal_id"]
        if not is_group_member(groups_table.get_item(Key={"group_id": group_id}).get("Item"), user):
            continue

        grant = {
            "object_id": object_id,
            "principal_id": user,
            "principal_type": "user",
            "object_type": record.get("object_type", "datasource"),
            "permission_level": "read",
            "policy": None,
            GRANT_SOURCE_ATTR: group_id,
        }
        try:
            # Never overwrite: a real owner/write record that appeared since our lookup must not be
            # downgraded to "read".
            table.put_item(Item=grant, ConditionExpression="attribute_not_exists(principal_id)")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Lost the race: hand back whatever is really there and let the caller judge it.
            return table.get_item(Key={"object_id": object_id, "principal_id": user}).get("Item")
        logger.info("Granted read on %s to %s via group %s", object_id, user, group_id)
        return grant
    return None


def grant_is_valid(table, groups_table, item, user):
    """
    False for a group-derived record whose user is no longer in that group (the record is deleted as a
    side effect). Records without the tag — real owner/write/read grants — are always valid.
    """
    if not item:
        return False
    group_id = item.get(GRANT_SOURCE_ATTR)
    if not group_id:
        return True
    if is_group_member(groups_table.get_item(Key={"group_id": group_id}).get("Item"), user):
        return True
    try:
        # Conditional on the tag so a record that was since replaced by a real grant is left alone.
        table.delete_item(
            Key={"object_id": item["object_id"], "principal_id": user},
            ConditionExpression=Attr(GRANT_SOURCE_ATTR).eq(group_id),
        )
        logger.info("Revoked stale group grant on %s for %s (left group %s)", item["object_id"], user, group_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return False


def revoke_group_grants(table, users, group_id):
    """
    Delete every group-derived record `users` hold because of `group_id`. Called when members are removed
    or the group is deleted. Returns how many records were removed. Uses the PrincipalIdIndex.
    """
    removed = 0
    for user in users or []:
        records = _query_all(
            table,
            IndexName="PrincipalIdIndex",
            KeyConditionExpression=Key("principal_id").eq(user),
            FilterExpression=Attr(GRANT_SOURCE_ATTR).eq(group_id),
        )
        for record in records:
            try:
                table.delete_item(
                    Key={"object_id": record["object_id"], "principal_id": user},
                    ConditionExpression=Attr(GRANT_SOURCE_ATTR).eq(group_id),
                )
                removed += 1
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
    if removed:
        logger.info("Revoked %d group-derived grants from %s for group %s", removed, list(users), group_id)
    return removed
