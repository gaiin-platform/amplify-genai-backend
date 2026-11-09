"""Unit tests for the conversation storage handlers (state/conversation.py).

boto3 / pycommon are stubbed so the tests run without AWS dependencies installed. The S3 client is a
small in-memory fake that supports exactly what the handlers use.
"""
import datetime as dt
import importlib
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).parents[1]
for path in (ROOT, ROOT / "state"):
    sys.path.insert(0, str(path))


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeBotoCoreError(Exception):
    pass


class FakeS3:
    """In-memory S3. Objects: {(bucket, key): (body_str, last_modified_datetime)}."""

    page_size = 2  # small on purpose, to exercise pagination

    def __init__(self):
        self.objects = {}
        self.clock = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def put(self, bucket, key, body):
        self.clock += dt.timedelta(seconds=10)
        self.objects[(bucket, key)] = (body, self.clock)

    def put_object(self, Bucket, Key, Body, **_):
        self.put(Bucket, Key, Body)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FakeClientError("NoSuchKey")
        body, modified = self.objects[(Bucket, Key)]
        return {"Body": SimpleNamespace(read=lambda: body.encode("utf-8")), "LastModified": modified}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FakeClientError("404")
        return {"LastModified": self.objects[(Bucket, Key)][1]}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3/{Params['Bucket']}/{Params['Key']}"

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for (b, k) in fake.objects if b == Bucket and k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), fake.page_size):
                    chunk = keys[i:i + fake.page_size]
                    yield {"Contents": [{"Key": k, "LastModified": fake.objects[(Bucket, k)][1]} for k in chunk]} if chunk else {}

        return Paginator()


def _install_stubs(fake_s3):
    def mod(name, **attrs):
        m = ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    boto3 = mod("boto3", client=lambda *_a, **_k: fake_s3,
                resource=lambda *_a, **_k: SimpleNamespace(Table=lambda _n: SimpleNamespace(put_item=lambda **_k: None, delete_item=lambda **_k: None)))
    mod("boto3.dynamodb")
    mod("boto3.dynamodb.conditions", Key=object, Attr=object)
    mod("botocore")
    mod("botocore.exceptions", BotoCoreError=FakeBotoCoreError, ClientError=FakeClientError)
    mod("pycommon")
    sys.modules["pycommon"].__path__ = []
    mod("pycommon.authz", validated=lambda *_a, **_k: (lambda fn: fn), setup_validated=lambda *_a, **_k: None)
    mod("pycommon.decorators", required_env_vars=lambda *_a, **_k: (lambda fn: fn))
    mod("pycommon.api")
    mod("pycommon.api.ops", api_tool=lambda *_a, **_k: (lambda fn: fn))
    mod("pycommon.db_utils", convert_floats_to_decimal=lambda x: x)
    mod("pycommon.dal")
    mod("pycommon.dal.providers")
    mod("pycommon.dal.providers.aws")
    ops = SimpleNamespace(__getattr__=None)

    class _Any:
        def __getattr__(self, name):
            return name

    mod("pycommon.dal.providers.aws.resource_perms", DynamoDBOperation=_Any(), S3Operation=_Any())
    # The real codec is tested in pycommon (tests/test_lzw*.py); here it is replaced per test, see setUp.
    mod("pycommon.lzw", lzw_compress=lambda s: json.loads(s), lzw_uncompress=_uncompress)
    mod("pycommon.logger", getLogger=lambda *_a, **_k: SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, debug=lambda *a, **k: None))
    mod("schemata")
    sys.modules["schemata"].__path__ = []
    mod("schemata.schema_validation_rules", rules={})
    mod("schemata.permissions", get_permission_checker=lambda *_a, **_k: None)


def _uncompress(data):
    """Test double for the codec: the "compressed" payload is the conversation dict itself."""
    if isinstance(data, dict):
        return data
    raise ValueError("Invalid compressed data")


BUCKET = "consol"
LEGACY = "legacy"
USER = "alice@example.com"


class ConversationStateTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        for name in list(sys.modules):
            if name == "conversation":
                del sys.modules[name]
        _install_stubs(self.s3)
        os.environ["S3_CONSOLIDATION_BUCKET_NAME"] = BUCKET
        os.environ["S3_CONVERSATIONS_BUCKET_NAME"] = LEGACY
        os.environ.pop("CONVERSATION_METADATA_TABLE", None)
        self.addCleanup(os.environ.pop, "CONVERSATION_METADATA_TABLE", None)
        self.conv = importlib.import_module("conversation")
        # Most tests use the identity "codec"; tests that need list-of-codes payloads call use_contract_codec().
        self.conv.lzw_compress = lambda s: json.loads(s)
        self.conv.lzw_uncompress = _uncompress

    def use_contract_codec(self):
        """A tiny stand-in with the SAME CONTRACT as pycommon.lzw (list of codes in, parsed JSON out; "" for an
        empty list; ValueError for undecodable or non-JSON data). The real LZW is covered by pycommon's tests."""
        codec = SimpleNamespace()

        def compress(text):
            return [ord(c) for c in text] if text else []

        def uncompress(codes):
            if not codes:
                return ""
            if not isinstance(codes, list) or any((not isinstance(c, int)) or c < 0 or c > 255 for c in codes):
                raise ValueError("Invalid compressed data")
            try:
                return json.loads("".join(chr(c) for c in codes))
            except json.JSONDecodeError:
                raise ValueError("Failed to parse JSON from decompressed string")

        codec.lzw_compress, codec.lzw_uncompress = compress, uncompress
        self.conv.lzw_compress, self.conv.lzw_uncompress = compress, uncompress
        return codec

    # -- helpers
    def store(self, cid, messages=("hi",), name=None, bucket=BUCKET, legacy=False):
        conv = {"id": cid, "name": name or cid, "messages": [{"content": m} for m in messages]}
        key = f"{USER}/{cid}" if legacy else f"conversations/{USER}/{cid}"
        self.s3.put(LEGACY if legacy else bucket, key, json.dumps({"conversation": conv, "folder": None}))
        return conv

    def upload(self, cid, messages, expected=None):
        data = {"conversation": {"id": cid, "name": cid, "messages": [{"content": m} for m in messages]},
                "conversationId": cid}
        if expected is not None:
            data["expectedVersion"] = expected
        return self.conv.upload_conversation({}, None, USER, "n", {"data": data})

    # -- pagination / robustness
    def test_listing_is_paginated_past_one_page(self):
        for i in range(7):
            self.store(f"c{i}")
        result = self.conv.get_all_complete_conversations(USER)
        self.assertEqual(len(result), 7)

    def test_one_corrupt_object_does_not_fail_the_whole_listing(self):
        self.store("good")
        self.s3.put(BUCKET, f"conversations/{USER}/bad", "{not json")
        self.s3.put(BUCKET, f"conversations/{USER}/bad2", json.dumps({"conversation": [1, 2, 3], "folder": None}))
        result = self.conv.get_all_complete_conversations(USER)
        self.assertEqual([c["conversation"]["id"] for c in result], ["good"])

    def test_consolidation_copy_wins_over_stale_legacy_copy(self):
        self.store("x", name="old name", legacy=True)
        self.store("x", name="new name")
        result = self.conv.get_all_complete_conversations(USER)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["conversation"]["name"], "new name")

    def test_listing_failure_is_reported_not_returned_as_partial(self):
        def boom(*_a, **_k):
            raise FakeClientError("AccessDenied")
        self.s3.get_paginator = boom
        self.assertIsNone(self.conv.get_all_complete_conversations(USER))

    def test_get_all_includes_message_count_date_and_record_id(self):
        conv = {"id": "a", "name": "a", "messages": [{"content": "1"}, {"content": "2"}],
                "date": "2026-01-01", "codeInterpreterRecordId": "rec"}
        self.s3.put(BUCKET, f"conversations/{USER}/a", json.dumps({"conversation": conv, "folder": None}))
        captured = {}
        self.conv.get_presigned_urls = lambda user, convs, *a: captured.setdefault("c", convs) and []
        self.conv.get_all_conversations({}, None, USER, "n", {})
        summary = captured["c"][0]["conversation"]
        self.assertEqual(summary["messageCount"], 2)
        self.assertEqual(summary["date"], "2026-01-01")
        self.assertEqual(summary["codeInterpreterRecordId"], "rec")
        self.assertNotIn("messages", summary)

    def test_presigned_chunk_keys_are_unique_per_request(self):
        convs = [{"conversation": {"id": "a"}}]
        self.conv.get_presigned_urls(USER, convs)
        self.conv.get_presigned_urls(USER, convs)
        temp_keys = [k for (_b, k) in self.s3.objects if k.startswith("temp/")]
        self.assertEqual(len(temp_keys), 2)

    # -- conflict + empty-overwrite guards
    def test_upload_reports_version_and_get_returns_it(self):
        result = self.upload("c1", ["a"])
        self.assertTrue(result["success"])
        got = self.conv.get_conversation({"queryStringParameters": {"conversationId": "c1"}}, None, USER, "n", {})
        self.assertEqual(got["version"], result["version"])

    def test_stale_upload_is_rejected_as_conflict(self):
        first = self.upload("c1", ["a"])
        self.upload("c1", ["a", "b"])  # someone else saves later
        stale = self.upload("c1", ["a", "c"], expected=first["version"])
        self.assertFalse(stale["success"])
        self.assertEqual(stale["reason"], "CONFLICT")
        stored = json.loads(self.s3.objects[(BUCKET, f"conversations/{USER}/c1")][0])
        self.assertEqual(len(stored["conversation"]["messages"]), 2)

    def test_upload_with_current_version_succeeds_and_no_baseline_is_unconditional(self):
        first = self.upload("c1", ["a"])
        self.assertTrue(self.upload("c1", ["a", "b"], expected=first["version"])["success"])
        self.assertTrue(self.upload("c1", ["a", "b", "c"])["success"])

    def test_empty_upload_cannot_replace_conversation_with_messages(self):
        self.upload("c1", ["a"])
        result = self.upload("c1", [])
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "EMPTY_OVERWRITE")
        stored = json.loads(self.s3.objects[(BUCKET, f"conversations/{USER}/c1")][0])
        self.assertEqual(len(stored["conversation"]["messages"]), 1)

    def test_empty_upload_of_new_conversation_is_allowed(self):
        self.assertTrue(self.upload("fresh", [])["success"])

    def test_presigned_url_checks_conflict(self):
        first = self.upload("c1", ["a"])
        self.upload("c1", ["a", "b"])
        res = self.conv.get_conversation_upload_url(
            {"queryStringParameters": {"conversationId": "c1", "expectedVersion": str(first["version"])}}, None, USER, "n", {})
        self.assertEqual(res.get("reason"), "CONFLICT")
        ok = self.conv.get_conversation_upload_url({"queryStringParameters": {"conversationId": "c1"}}, None, USER, "n", {})
        self.assertTrue(ok["success"])

    # -- ids
    def test_invalid_ids_get_a_proper_error_response(self):
        res = self.conv.get_conversation({"queryStringParameters": {"conversationId": "../x"}}, None, USER, "n", {})
        self.assertFalse(res["success"])
        res = self.conv.delete_conversation({"queryStringParameters": {}}, None, USER, "n", {})
        self.assertFalse(res["success"])

    def test_non_uuid_ids_are_accepted(self):
        self.assertTrue(self.upload("imported_chat-1", ["a"])["success"])
        got = self.conv.get_conversation({"queryStringParameters": {"conversationId": "imported_chat-1"}}, None, USER, "n", {})
        self.assertTrue(got["success"])

    # -- empty-conversation listing
    def test_get_empty_treats_compressed_form_and_empty_account_correctly(self):
        self.assertTrue(self.conv.get_empty_conversations({}, None, USER, "n", {})["success"])  # empty account is not a failure
        self.store("full", messages=("x",))
        self.s3.put(BUCKET, f"conversations/{USER}/stripped", json.dumps(
            {"conversation": {"id": "stripped", "name": "s", "messages": [], "compressedMessages": [1, 2]}, "folder": None}))
        self.s3.put(BUCKET, f"conversations/{USER}/blank", json.dumps(
            {"conversation": {"id": "blank", "name": "b", "messages": []}, "folder": None}))
        res = self.conv.get_empty_conversations({}, None, USER, "n", {})
        self.assertEqual(sorted(res["nonEmptyIds"]), ["full", "stripped"])


    # -- id validation
    def test_id_validator_accepts_agent_and_imported_ids(self):
        ok = self.conv.is_valid_conversation_id
        for cid in ["3f2c9d1e-1111-4222-8333-444455556666", "agent:run=1@x+y_z.v2", "a.b", "..a", "a..", "x" * 256]:
            self.assertTrue(ok(cid), cid)

    def test_id_validator_rejects_unsafe_ids(self):
        ok = self.conv.is_valid_conversation_id
        for cid in [".", "..", "", "a/b", "../x", "a b", "a\n", "a\t", "a\x00", "x" * 257, None, 5, ["a"]]:
            self.assertFalse(ok(cid), repr(cid))

    def test_delete_multiple_puts_invalid_ids_in_failed_and_never_touches_s3(self):
        self.store("good")
        os.environ["CONVERSATION_METADATA_TABLE"] = "meta"
        deleted = []
        real_delete = self.s3.delete_object
        self.s3.delete_object = lambda Bucket, Key: (deleted.append((Bucket, Key)), real_delete(Bucket=Bucket, Key=Key))
        res = self.conv.delete_multiple_conversations(
            {}, None, USER, "n", {"data": {"conversationIds": ["good", "..", "a/b", "x y"]}})
        self.assertFalse(res["success"])
        self.assertEqual(res["failed"], ["..", "a/b", "x y"])
        self.assertEqual(res["successful"], ["good"])
        for _bucket, key in deleted:
            self.assertEqual(key.split("/")[-1], "good")

    def test_register_conversation_id_handling(self):
        base = {"name": "n", "messages": [{"role": "user", "content": "hi"}]}
        bad = self.conv.register_conversation({}, None, USER, "n", {"data": dict(base, id="../evil")})
        self.assertEqual(bad["reason"], "INVALID_ID")
        self.assertFalse(bad["success"])
        self.assertEqual([k for (_b, k) in self.s3.objects], [])
        ok = self.conv.register_conversation({}, None, USER, "n", {"data": dict(base, id="agent:1")})
        self.assertTrue(ok["success"])
        self.assertIn((BUCKET, f"conversations/{USER}/agent:1"), self.s3.objects)
        generated = self.conv.register_conversation({}, None, USER, "n", {"data": dict(base)})
        self.assertTrue(generated["success"])
        self.assertEqual(len(self.s3.objects), 2)

    def test_get_multiple_rejects_invalid_ids(self):
        self.store("good")
        self.conv.get_presigned_urls = lambda user, convs, *a: [c["id"] for c in convs]
        res = self.conv.get_multiple_conversations({}, None, USER, "n", {"data": {"conversationIds": ["good", "../x"]}})
        self.assertEqual(res["failed"], ["../x"])
        self.assertEqual(res["presignedUrls"], ["good"])

    # -- upload payload guard
    def test_unreadable_payloads_are_rejected_without_writing(self):
        for payload in ([1, 2, 3], "garbage", None):
            res = self.conv.upload_conversation(
                {}, None, USER, "n", {"data": {"conversation": payload, "conversationId": "c1"}})
            self.assertEqual(res["reason"], "INVALID_PAYLOAD", payload)
            self.assertFalse(res["success"])
        self.assertEqual(self.s3.objects, {})

    def test_payload_guard_with_pycommon_codec_contract(self):
        codec = self.use_contract_codec()

        def up(payload):
            return self.conv.upload_conversation({}, None, USER, "n", {"data": {"conversation": payload, "conversationId": "c1"}})

        # decodes to "" (empty list), to a JSON list, to a JSON string: all unreadable as a conversation
        for payload in ([], codec.lzw_compress("[1,2]"), codec.lzw_compress('"just text"'), [999999]):
            res = up(payload)
            self.assertEqual(res["reason"], "INVALID_PAYLOAD", payload)
        self.assertEqual(self.s3.objects, {})
        conv = {"id": "c1", "name": "caf\u00e9 \u0142\u00f3d\u017a \U0001F600", "messages": [{"content": "U+0041"}]}
        res = up(codec.lzw_compress(json.dumps(conv)))
        self.assertTrue(res["success"])
        stored = json.loads(self.s3.objects[(BUCKET, f"conversations/{USER}/c1")][0])
        self.assertEqual(codec.lzw_uncompress(stored["conversation"]), conv)

    def test_empty_overwrite_guard_with_pycommon_codec_contract(self):
        codec = self.use_contract_codec()
        full = codec.lzw_compress(json.dumps({"id": "c1", "name": "c1", "messages": [{"content": "a"}]}))
        empty = codec.lzw_compress(json.dumps({"id": "c1", "name": "c1", "messages": []}))
        def up(payload):
            return self.conv.upload_conversation({}, None, USER, "n", {"data": {"conversation": payload, "conversationId": "c1"}})
        self.assertTrue(up(full)["success"])
        self.assertEqual(up(empty)["reason"], "EMPTY_OVERWRITE")

    # -- expectedVersion handling
    def test_non_integer_expected_version_is_a_clean_failure(self):
        self.upload("c1", ["a"])
        for bad in ("abc", [1], {"a": 1}, float("nan"), float("inf")):
            data = {"conversation": {"id": "c1", "name": "c1", "messages": [{"content": "x"}]},
                    "conversationId": "c1", "expectedVersion": bad}
            res = self.conv.upload_conversation({}, None, USER, "n", {"data": data})
            self.assertEqual(res["reason"], "INVALID_VERSION", bad)
        # numeric strings and floats are tolerated
        self.assertIsNone(self.conv.check_upload_allowed(self.s3, f"conversations/{USER}/c1", "99999999999999"))
        self.assertIsNone(self.conv.check_upload_allowed(self.s3, f"conversations/{USER}/c1", 9.9e15))

    # -- tolerant listings
    def test_since_timestamp_skips_unreadable_objects(self):
        self.store("good")
        self.s3.put(BUCKET, f"conversations/{USER}/bad", "{not json")
        self.s3.put(BUCKET, f"conversations/{USER}/bad2", json.dumps({"conversation": [1, 2], "folder": None}))
        self.s3.put(BUCKET, f"conversations/{USER}/bad3", json.dumps({"nope": 1}))
        captured = {}
        self.conv.get_presigned_urls = lambda user, convs, *a: captured.setdefault("c", convs) and ["url"]
        res = self.conv.get_conversations_since_timestamp(
            {"pathParameters": {"timestamp": "0"}}, None, USER, "n", {})
        self.assertTrue(res["success"])
        self.assertEqual([c["conversation"]["id"] for c in captured["c"]], ["good"])

    def test_metadata_lightweight_falls_back_to_basic_entry_on_decode_failure(self):
        self.store("good", name="Good")
        self.s3.put(BUCKET, f"conversations/{USER}/bad", "{not json")
        self.s3.put(BUCKET, f"conversations/{USER}/bad2", json.dumps({"conversation": [1, 2], "folder": None}))
        meta = {m["id"]: m for m in self.conv.get_conversations_metadata_lightweight(USER)}
        self.assertEqual(set(meta), {"good", "bad", "bad2"})
        self.assertEqual(meta["good"]["name"], "Good")
        self.assertEqual(meta["bad"]["name"], "Conversation bad")
        self.assertIsInstance(meta["bad2"]["lastModified"], int)

    def test_complete_listing_only_keeps_dict_conversations(self):
        self.store("good")
        self.conv.lzw_uncompress = lambda data: data  # identity: lets non-dict payloads through the codec
        self.s3.put(BUCKET, f"conversations/{USER}/lst", json.dumps({"conversation": ["a"], "folder": None}))
        self.s3.put(BUCKET, f"conversations/{USER}/str", json.dumps({"conversation": "text", "folder": None}))
        result = self.conv.get_all_complete_conversations(USER)
        self.assertEqual([c["conversation"]["id"] for c in result], ["good"])
        res = self.conv.get_all_conversations({}, None, USER, "n", {})
        self.assertTrue(res["success"])

    def test_get_all_conversations_survives_odd_items(self):
        self.conv.get_all_complete_conversations_ex = lambda *a, **k: ([{"folder": None}, "x", {"conversation": 5}], False)
        self.conv.get_presigned_urls = lambda user, convs, *a: ["n=%d" % len(convs)]
        res = self.conv.get_all_conversations({}, None, USER, "n", {})
        self.assertEqual(res, {"success": True, "presignedUrls": ["n=0"]})

    # -- time budget
    def test_listing_is_newest_first(self):
        for i in range(5):
            self.store(f"c{i}")  # later put => newer
        ids = [c["conversation"]["id"] for c in self.conv.get_all_complete_conversations(USER)]
        self.assertEqual(ids, ["c4", "c3", "c2", "c1", "c0"])

    def _blocking_s3(self, release):
        """After the first get_object, further downloads block until `release` is set."""
        real_get = self.s3.get_object
        calls = []

        def get_object(Bucket, Key):
            calls.append(Key)
            if len(calls) > 1:
                release.wait(5)
            return real_get(Bucket=Bucket, Key=Key)

        self.s3.get_object = get_object
        return calls

    def test_time_budget_returns_newest_so_far_and_flags_truncation(self):
        for i in range(6):
            self.store(f"c{i}")
        self.conv._S3_FETCH_WORKERS = 1
        release = threading.Event()
        self._blocking_s3(release)
        try:
            convs, truncated = self.conv.get_all_complete_conversations_ex(USER, time_budget_seconds=0.2)
        finally:
            release.set()
        self.assertTrue(truncated)
        self.assertEqual([c["conversation"]["id"] for c in convs], ["c5"])

    def test_no_truncation_when_budget_is_sufficient(self):
        self.store("a")
        convs, truncated = self.conv.get_all_complete_conversations_ex(USER, time_budget_seconds=30)
        self.assertFalse(truncated)
        self.assertEqual(len(convs), 1)
        convs, truncated = self.conv.get_all_complete_conversations_ex(USER, time_budget_seconds=None)
        self.assertFalse(truncated)

    def test_fake_clock_expiring_before_collection_truncates(self):
        for i in range(3):
            self.store(f"c{i}")
        self.conv._S3_FETCH_WORKERS = 1
        release = threading.Event()
        self._blocking_s3(release)
        ticks = iter([0.0] + [100.0] * 50)  # deadline = 0 + budget; every later reading is far past it
        self.conv._monotonic = lambda: next(ticks)
        try:
            convs, truncated = self.conv.get_all_complete_conversations_ex(USER, time_budget_seconds=1)
        finally:
            release.set()
        self.assertTrue(truncated)
        self.assertLessEqual(len(convs), 1)

    def test_get_all_conversations_reports_truncation(self):
        self.store("a")
        self.conv.get_presigned_urls = lambda user, convs, *a: ["u"]
        self.conv.get_all_complete_conversations_ex = lambda *a, **k: (
            [{"conversation": {"id": "a", "messages": []}, "folder": None}], True)
        res = self.conv.get_all_conversations({}, None, USER, "n", {})
        self.assertTrue(res["truncated"])
        self.assertTrue(res["success"])
        self.conv.get_all_complete_conversations_ex = lambda *a, **k: ([], True)
        self.assertTrue(self.conv.get_all_conversations({}, None, USER, "n", {})["truncated"])
        self.conv.get_all_complete_conversations_ex = lambda *a, **k: (
            [{"conversation": {"id": "a", "messages": []}, "folder": None}], False)
        self.assertNotIn("truncated", self.conv.get_all_conversations({}, None, USER, "n", {}))

    def test_get_empty_conversations_refuses_truncated_listing(self):
        self.conv.get_all_complete_conversations_ex = lambda *a, **k: (
            [{"conversation": {"id": "blank", "messages": []}, "folder": None}], True)
        res = self.conv.get_empty_conversations({}, None, USER, "n", {})
        self.assertEqual(res, {"success": False, "message": "Too many conversations to scan at once", "truncated": True})


if __name__ == "__main__":
    unittest.main()
