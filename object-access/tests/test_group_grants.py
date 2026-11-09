"""
Unit tests for group_grants.py — run with:  python3 -m unittest discover -s object-access/tests

boto3/botocore aren't needed: the two names the module imports are stubbed, and DynamoDB is replaced by an
in-memory table that behaves like the real one where it matters here (pagination, conditional writes,
the PrincipalIdIndex).
"""
import os
import sys
import types
import unittest

# ── stand-ins for boto3.dynamodb.conditions and botocore.exceptions ──────────────
class _Cond:
    def __init__(self, kind, name, value):
        self.kind, self.name, self.value = kind, name, value

class Key:
    def __init__(self, name): self.name = name
    def eq(self, value): return _Cond("key", self.name, value)

class Attr(Key):
    def eq(self, value): return _Cond("attr", self.name, value)

class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}

for name in ("boto3", "boto3.dynamodb", "botocore"):
    sys.modules.setdefault(name, types.ModuleType(name))
conditions = types.ModuleType("boto3.dynamodb.conditions"); conditions.Key = Key; conditions.Attr = Attr
exceptions = types.ModuleType("botocore.exceptions"); exceptions.ClientError = ClientError
sys.modules["boto3.dynamodb.conditions"] = conditions
sys.modules["botocore.exceptions"] = exceptions

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import group_grants as gg  # noqa: E402


class FakeTable:
    """Keyed by (object_id, principal_id); queries page two items at a time."""
    PAGE = 2

    def __init__(self, items=()):
        self.items = {(i["object_id"], i["principal_id"]): dict(i) for i in items}
        self.deleted = []

    def get_item(self, Key):
        item = self.items.get((Key.get("object_id"), Key.get("principal_id")))
        if "group_id" in Key:  # groups table
            item = self.items.get(Key["group_id"])
        return {"Item": dict(item)} if item else {}

    def query(self, KeyConditionExpression, IndexName=None, FilterExpression=None, ExclusiveStartKey=None):
        field = "principal_id" if IndexName == "PrincipalIdIndex" else "object_id"
        rows = sorted((v for v in self.items.values() if v[field] == KeyConditionExpression.value),
                      key=lambda v: (v["object_id"], v["principal_id"]))
        start = ExclusiveStartKey["i"] if ExclusiveStartKey else 0
        page = rows[start:start + self.PAGE]
        if FilterExpression is not None:
            page = [r for r in page if r.get(FilterExpression.name) == FilterExpression.value]
        out = {"Items": [dict(r) for r in page]}
        if start + self.PAGE < len(rows):
            out["LastEvaluatedKey"] = {"i": start + self.PAGE}
        return out

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["object_id"], Item["principal_id"])
        if ConditionExpression == "attribute_not_exists(principal_id)" and key in self.items:
            raise ClientError("ConditionalCheckFailedException")
        self.items[key] = dict(Item)

    def delete_item(self, Key, ConditionExpression=None):
        key = (Key["object_id"], Key["principal_id"])
        existing = self.items.get(key)
        if ConditionExpression is not None and (not existing or existing.get(ConditionExpression.name) != ConditionExpression.value):
            raise ClientError("ConditionalCheckFailedException")
        self.items.pop(key, None)
        self.deleted.append(key)


class FakeGroups:
    def __init__(self, groups): self.groups = groups
    def get_item(self, Key): return {"Item": self.groups[Key["group_id"]]} if Key["group_id"] in self.groups else {}


def group_record(obj="obj1", group="g1", level="read", ptype="group"):
    return {"object_id": obj, "principal_id": group, "principal_type": ptype, "object_type": "datasource", "permission_level": level, "policy": None}


GROUPS = FakeGroups({"g1": {"members": {"alice": "read"}, "systemUsers": ["bot"]}, "g2": {"members": {}}})


class GrantTests(unittest.TestCase):
    def test_member_gets_a_tagged_read_record(self):
        table = FakeTable([group_record()])
        grant = gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice")
        self.assertEqual(grant["permission_level"], "read")
        self.assertEqual(grant[gg.GRANT_SOURCE_ATTR], "g1")
        self.assertEqual(table.items[("obj1", "alice")][gg.GRANT_SOURCE_ATTR], "g1")  # persisted

    def test_system_users_count_as_members(self):
        table = FakeTable([group_record()])
        self.assertIsNotNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "bot"))

    def test_non_members_and_other_access_types_get_nothing(self):
        table = FakeTable([group_record()])
        self.assertIsNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "mallory"))
        self.assertIsNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "write", "alice"))
        self.assertNotIn(("obj1", "mallory"), table.items)
        self.assertNotIn(("obj1", "alice"), table.items)

    def test_a_group_record_that_grants_nothing_grants_nothing(self):
        table = FakeTable([group_record(level="none")])
        self.assertIsNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice"))

    def test_only_group_principals_count(self):
        table = FakeTable([group_record(ptype="user")])
        self.assertIsNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice"))

    def test_does_not_downgrade_an_existing_owner_record(self):
        owner = {"object_id": "obj1", "principal_id": "alice", "principal_type": "user", "permission_level": "owner"}
        table = FakeTable([group_record(), owner])
        result = gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice")
        self.assertEqual(result["permission_level"], "owner")
        self.assertEqual(table.items[("obj1", "alice")]["permission_level"], "owner")

    def test_finds_the_group_record_on_a_later_page(self):
        filler = [{"object_id": "obj1", "principal_id": f"a{i}", "principal_type": "user", "permission_level": "read"} for i in range(5)]
        table = FakeTable(filler + [group_record(group="g1")])
        self.assertIsNotNone(gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice"))


class ValidityTests(unittest.TestCase):
    def test_a_real_record_is_always_valid(self):
        real = {"object_id": "obj1", "principal_id": "bob", "permission_level": "owner"}
        self.assertTrue(gg.grant_is_valid(FakeTable([real]), GROUPS, real, "bob"))

    def test_a_tagged_record_stays_valid_while_the_user_is_a_member(self):
        table = FakeTable([group_record()])
        grant = gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice")
        self.assertTrue(gg.grant_is_valid(table, GROUPS, grant, "alice"))
        self.assertIn(("obj1", "alice"), table.items)

    def test_leaving_the_group_voids_and_deletes_the_grant(self):
        table = FakeTable([group_record()])
        grant = gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice")
        left = FakeGroups({"g1": {"members": {}}})
        self.assertFalse(gg.grant_is_valid(table, left, grant, "alice"))
        self.assertNotIn(("obj1", "alice"), table.items)

    def test_a_deleted_group_voids_the_grant(self):
        table = FakeTable([group_record()])
        grant = gg.grant_read_via_group_membership(table, GROUPS, "obj1", "read", "alice")
        self.assertFalse(gg.grant_is_valid(table, FakeGroups({}), grant, "alice"))

    def test_a_record_replaced_by_a_real_grant_is_not_deleted(self):
        tagged_copy = {"object_id": "obj1", "principal_id": "alice", gg.GRANT_SOURCE_ATTR: "g1"}
        real = {"object_id": "obj1", "principal_id": "alice", "permission_level": "owner"}
        table = FakeTable([real])
        self.assertFalse(gg.grant_is_valid(table, FakeGroups({"g1": {"members": {}}}), tagged_copy, "alice"))
        self.assertIn(("obj1", "alice"), table.items)  # the owner record survived

    def test_missing_item_is_invalid(self):
        self.assertFalse(gg.grant_is_valid(FakeTable(), GROUPS, None, "alice"))


class RevocationTests(unittest.TestCase):
    def tagged(self, obj, user, group):
        return {"object_id": obj, "principal_id": user, "principal_type": "user", "permission_level": "read", gg.GRANT_SOURCE_ATTR: group}

    def test_removes_only_that_groups_tagged_records_for_that_user(self):
        table = FakeTable([
            self.tagged("o1", "alice", "g1"), self.tagged("o2", "alice", "g1"), self.tagged("o3", "alice", "g1"),
            self.tagged("o4", "alice", "g2"),                                              # another group's grant
            {"object_id": "o5", "principal_id": "alice", "permission_level": "owner"},     # her own file
            self.tagged("o1", "carol", "g1"),                                              # someone else
        ])
        self.assertEqual(gg.revoke_group_grants(table, ["alice"], "g1"), 3)
        remaining = set(table.items)
        self.assertEqual(remaining, {("o4", "alice"), ("o5", "alice"), ("o1", "carol")})

    def test_handles_many_users_and_nothing_to_remove(self):
        table = FakeTable([self.tagged("o1", "alice", "g1"), self.tagged("o2", "bob", "g1")])
        self.assertEqual(gg.revoke_group_grants(table, ["alice", "bob", "nobody"], "g1"), 2)
        self.assertEqual(gg.revoke_group_grants(table, [], "g1"), 0)
        self.assertEqual(gg.revoke_group_grants(table, None, "g1"), 0)


if __name__ == "__main__":
    unittest.main()
