"""Tests for the idle code interpreter session reaper.

AWS/pycommon imports are stubbed so this runs with plain `python -m unittest`.
"""
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "codeinterpreter" / "code_interpreter_api.py"
NOW = 10_000_000_000
IDLE_MS = 15 * 60 * 1000


class FakeClientError(Exception):
    def __init__(self, code, message="boom"):
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


class FakeTable:
    """In-memory table that understands only the expressions the reaper/touch use."""

    def __init__(self, items, page_size=None):
        self.items = {i["id"]: dict(i) for i in items}
        self.page_size = page_size
        self.before_update = None  # hook: simulates a concurrent chat message

    def query(self, **kw):
        assert kw["IndexName"] == "ActivityIndex"
        vals = kw["ExpressionAttributeValues"]
        hits = sorted(
            (i for i in self.items.values()
             if i.get("activityBucket") == vals[":v"] and i["updatedAt"] < vals[":cutoff"]),
            key=lambda i: i["updatedAt"],
        )
        start = 0
        if "ExclusiveStartKey" in kw:
            # Resume by sort-key position (as DynamoDB does); the reaper may have removed
            # earlier items from the index meanwhile.
            last = kw["ExclusiveStartKey"]["updatedAt"]
            start = next((n for n, i in enumerate(hits) if i["updatedAt"] > last), len(hits))
        size = self.page_size or len(hits)
        page = hits[start:start + size]
        out = {"Items": [dict(i) for i in page]}
        if start + size < len(hits):
            out["LastEvaluatedKey"] = {"id": page[-1]["id"], "updatedAt": page[-1]["updatedAt"]}
        return out

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None, **kw):
        if self.before_update:
            hook, self.before_update = self.before_update, None
            hook(self)
        item = self.items[Key["id"]]
        vals = ExpressionAttributeValues or {}
        cond = kw.get("ConditionExpression")
        if cond and item["updatedAt"] != vals[":seen"]:
            raise FakeClientError("ConditionalCheckFailedException")
        if UpdateExpression.startswith("REMOVE"):
            item.pop("activityBucket", None)
        elif UpdateExpression == "SET #a = :v":
            item["activityBucket"] = vals[":v"]
        elif UpdateExpression == "SET updatedAt = :ts, #a = :v":
            item["updatedAt"] = vals[":ts"]
            item["activityBucket"] = vals[":v"]
        else:
            raise AssertionError(f"unexpected expression {UpdateExpression}")


def load_module():
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *a, **k: None
    boto3.resource = lambda *a, **k: None
    botocore = types.ModuleType("botocore")
    botocore_exc = types.ModuleType("botocore.exceptions")
    botocore_exc.ClientError = FakeClientError
    botocore_exc.NoCredentialsError = type("NoCredentialsError", (Exception,), {})
    pil = types.ModuleType("PIL")
    pil.Image = types.ModuleType("PIL.Image")
    pycommon = types.ModuleType("pycommon")
    api = types.ModuleType("pycommon.api")
    request_state = types.ModuleType("pycommon.api.request_state")
    request_state.request_killed = None
    logger_mod = types.ModuleType("pycommon.logger")

    class _Log:
        def __getattr__(self, _):
            return lambda *a, **k: None

    logger_mod.getLogger = lambda name: _Log()
    stubs = {
        "boto3": boto3, "botocore": botocore, "botocore.exceptions": botocore_exc,
        "PIL": pil, "PIL.Image": pil.Image, "pycommon": pycommon, "pycommon.api": api,
        "pycommon.api.request_state": request_state, "pycommon.logger": logger_mod,
    }
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location("ci_api_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rec(rid, updated_at, session="sess", bucket=True):
    item = {"id": rid, "user": "u", "updatedAt": updated_at, "data": {"sessionId": session}}
    if session is None:
        item["data"] = {}
    if bucket:
        item["activityBucket"] = "live"
    return item


class FakeAgentCore:
    def __init__(self, fail=None):
        self.stopped = []
        self.fail = fail or {}  # sessionId -> error code

    def stop_code_interpreter_session(self, codeInterpreterIdentifier, sessionId):
        if sessionId in self.fail:
            raise FakeClientError(self.fail[sessionId])
        self.stopped.append(sessionId)


class ReaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_module()

    def setUp(self):
        os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"] = "t"

    def run_reaper(self, table, agentcore=None):
        agentcore = agentcore or FakeAgentCore()
        self.api.agentcore_client = agentcore
        self.api.boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda name: table)
        stats = self.api.reap_idle_sessions(now_ms=NOW)
        return stats, agentcore

    def test_stops_only_sessions_idle_past_threshold_and_keeps_records(self):
        table = FakeTable([
            rec("idle", NOW - IDLE_MS - 1, "s-idle"),
            rec("fresh", NOW - IDLE_MS + 1000, "s-fresh"),
            rec("legacy", NOW - 10 * IDLE_MS, "s-legacy", bucket=False),  # not in the index
        ])
        stats, agentcore = self.run_reaper(table)
        self.assertEqual(agentcore.stopped, ["s-idle"])
        self.assertEqual(stats, {"stopped": 1, "skipped": 0, "failed": 0})
        self.assertNotIn("activityBucket", table.items["idle"])
        self.assertEqual(table.items["idle"]["data"]["sessionId"], "s-idle")  # renewal path keeps working
        self.assertIn("activityBucket", table.items["fresh"])

    def test_message_arriving_mid_sweep_wins(self):
        table = FakeTable([rec("busy", NOW - IDLE_MS - 1, "s-busy")])

        def new_message(t):
            t.items["busy"]["updatedAt"] = NOW  # chat touched it after we queried

        table.before_update = new_message
        stats, agentcore = self.run_reaper(table)
        self.assertEqual(agentcore.stopped, [])
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(table.items["busy"]["activityBucket"], "live")

    def test_unexpected_stop_failure_requeues_record_for_retry(self):
        table = FakeTable([rec("a", NOW - IDLE_MS - 1, "s-a")])
        stats, _ = self.run_reaper(table, FakeAgentCore(fail={"s-a": "ThrottlingException"}))
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(table.items["a"]["activityBucket"], "live")
        stats, agentcore = self.run_reaper(table)  # next sweep succeeds
        self.assertEqual((stats["stopped"], agentcore.stopped), (1, ["s-a"]))

    def test_already_gone_session_counts_as_stopped(self):
        table = FakeTable([rec("a", NOW - IDLE_MS - 1, "s-a")])
        stats, _ = self.run_reaper(table, FakeAgentCore(fail={"s-a": "ResourceNotFoundException"}))
        self.assertEqual(stats, {"stopped": 1, "skipped": 0, "failed": 0})
        self.assertNotIn("activityBucket", table.items["a"])

    def test_record_without_session_id_just_leaves_index(self):
        table = FakeTable([rec("a", NOW - IDLE_MS - 1, None)])
        stats, agentcore = self.run_reaper(table)
        self.assertEqual((stats["stopped"], agentcore.stopped), (1, []))
        self.assertNotIn("activityBucket", table.items["a"])

    def test_follows_pagination(self):
        table = FakeTable([rec(f"r{i}", NOW - IDLE_MS - 10 - i, f"s{i}") for i in range(5)], page_size=2)
        stats, agentcore = self.run_reaper(table)
        self.assertEqual(stats["stopped"], 5)
        self.assertEqual(sorted(agentcore.stopped), [f"s{i}" for i in range(5)])

    def test_touch_resets_idle_clock_and_rejoins_index(self):
        table = FakeTable([rec("a", 1, "s-a", bucket=False)])
        self.api.boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda name: table)
        self.api.touch_record("a")
        self.assertGreater(table.items["a"]["updatedAt"], 1)
        self.assertEqual(table.items["a"]["activityBucket"], "live")

    def test_touch_failure_does_not_raise(self):
        class Boom:
            def update_item(self, **kw):
                raise FakeClientError("InternalServerError")

        self.api.boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda name: Boom())
        self.api.touch_record("a")  # must not raise


if __name__ == "__main__":
    unittest.main()
