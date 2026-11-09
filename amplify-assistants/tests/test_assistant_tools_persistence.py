"""Tests for how assistants persist `tools` and how a shared copy is built.

core.py's AWS / pycommon imports are stubbed so this runs with plain
`python -m unittest` (same approach as test_idle_session_reaper.py).
"""
import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "service" / "core.py"


def _passthrough(*_args, **_kwargs):
    """Stand-in for decorator factories like @validated(...) / @required_env_vars(...)."""
    def wrap(fn):
        return fn
    if len(_args) == 1 and callable(_args[0]) and not _kwargs:
        return _args[0]
    return wrap


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def load_core():
    boto3 = _stub("boto3", resource=mock.MagicMock(), client=mock.MagicMock())
    _stub("boto3.dynamodb")
    _stub("boto3.dynamodb.conditions", Key=mock.MagicMock(), Attr=mock.MagicMock())
    _stub("botocore")
    _stub("botocore.exceptions", ClientError=type("ClientError", (Exception,), {}))
    _stub("requests")

    _stub("pycommon")
    _stub("pycommon.const", APIAccessType=mock.MagicMock())
    _stub("pycommon.api")
    _stub("pycommon.api.amplify_users", are_valid_amplify_users=mock.MagicMock())
    _stub("pycommon.api.files", delete_file=mock.MagicMock())
    _stub("pycommon.api.user_data", save_user_data=mock.MagicMock(return_value=True))
    _stub("pycommon.api.critical_logging", log_critical_error=mock.MagicMock(), SEVERITY_HIGH="high")
    _stub("pycommon.logger", getLogger=lambda *_: mock.MagicMock())
    _stub(
        "pycommon.api.data_sources",
        get_data_source_keys=mock.MagicMock(),
        translate_user_data_sources_to_hash_data_sources=mock.MagicMock(),
        extract_key=mock.MagicMock(),
    )
    _stub(
        "pycommon.api.object_permissions",
        update_object_permissions=mock.MagicMock(),
        can_access_objects=mock.MagicMock(),
        simulate_can_access_objects=mock.MagicMock(),
    )
    _stub("pycommon.api.ops", api_tool=_passthrough)
    _stub("pycommon.decorators", required_env_vars=_passthrough)
    _stub(
        "pycommon.dal.providers.aws.resource_perms",
        DynamoDBOperation=mock.MagicMock(),
        S3Operation=mock.MagicMock(),
    )
    for parent in ("pycommon.dal", "pycommon.dal.providers", "pycommon.dal.providers.aws"):
        _stub(parent)
    _stub(
        "pycommon.authz",
        validated=_passthrough,
        setup_validated=mock.MagicMock(),
        add_api_access_types=mock.MagicMock(),
    )
    _stub("schemata")
    _stub("schemata.schema_validation_rules", rules={})
    _stub("schemata.permissions", get_permission_checker=mock.MagicMock())
    _stub("pycommon.encoders", CustomPydanticJSONEncoder=json.JSONEncoder)

    spec = importlib.util.spec_from_file_location("assistants_core_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core()


def outlook_op(name):
    return {
        "name": name,
        "type": "integration",
        "tags": ["default", "integration", "microsoft_outlook"],
        "parameters": {"type": "object", "properties": {}},
    }


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


def save(tools, table=None, data=None):
    table = table or FakeTable()
    return core.save_assistant(
        table, "Email Assistant", "", "Help with email", data if data is not None else {},
        "", [], "amplify", tools, "owner@example.edu", 1, [],
    )


class PersistableToolsTest(unittest.TestCase):
    def test_returns_the_list_unchanged(self):
        tools = [outlook_op("microsoftListMessages")]
        self.assertEqual(core.persistable_tools(tools), tools)

    def test_non_lists_and_empty_store_as_empty_list(self):
        for value in (None, "x", {}, 3, []):
            self.assertEqual(core.persistable_tools(value), [])

    def test_unserialisable_tools_store_as_empty_list(self):
        class Unserialisable:
            def __str__(self):
                raise TypeError("no")

        self.assertEqual(core.persistable_tools([Unserialisable()]), [])

    def test_oversized_tools_are_skipped_not_fatal(self):
        huge = [{"name": "op", "description": "x" * (core.MAX_PERSISTED_TOOLS_BYTES + 1)}]
        self.assertEqual(core.persistable_tools(huge), [])


class SaveAssistantToolsTest(unittest.TestCase):
    def test_tools_are_stored_on_the_item(self):
        tools = [outlook_op("microsoftListMessages"), outlook_op("microsoftGetMessageDetails")]
        table = FakeTable()
        item = save(tools, table)
        self.assertEqual(item["tools"], tools)
        self.assertEqual(table.items[0]["tools"], tools)

    def test_tools_still_feed_the_version_hash(self):
        one = save([outlook_op("a")])
        two = save([outlook_op("a"), outlook_op("b")])
        self.assertNotEqual(one["coreHash"], two["coreHash"])

    def test_missing_tools_store_an_empty_list(self):
        self.assertEqual(save(None)["tools"], [])


class AssistantShareSaveTest(unittest.TestCase):
    def setUp(self):
        self.puts = []
        s3 = mock.MagicMock()
        s3.put_object.side_effect = lambda **kw: self.puts.append(kw)
        core.boto3.client = mock.MagicMock(return_value=s3)
        core.os.environ["S3_CONSOLIDATION_BUCKET_NAME"] = "bucket"
        self.entry = {
            "id": "ast/1",
            "name": "Email Assistant",
            "description": "",
            "instructions": "Help me with email",
            "tools": [outlook_op("microsoftListMessages")],
            "fileKeys": ["private/file"],
            "data": {
                "access": {"read": True, "write": True},
                "operations": [outlook_op("microsoftListMessages")],
                "builtInOperations": ["web_search"],
            },
        }

    def shared(self):
        result = core.assistant_share_save("token", "owner@example.edu", "bob@example.edu", "", self.entry)
        self.assertTrue(result["success"])
        return json.loads(self.puts[-1]["Body"])["prompts"][0]

    def test_clears_files_and_tools_on_the_copy(self):
        definition = self.shared()["data"]["assistant"]["definition"]
        self.assertEqual(definition["tools"], [])
        self.assertEqual(definition["fileKeys"], [])

    def test_keeps_operations_since_they_run_on_the_recipients_own_connection(self):
        prompt = self.shared()
        for ops in (prompt["data"]["operations"], prompt["data"]["assistant"]["definition"]["data"]["operations"]):
            self.assertEqual([o["name"] for o in ops], ["microsoftListMessages"])
        self.assertEqual(prompt["data"]["builtInOperations"], ["web_search"])

    def test_copy_is_read_only(self):
        data = self.shared()["data"]
        self.assertFalse(data["access"]["write"])
        self.assertTrue(data["noEdit"] and data["noCopy"] and data["noShare"])

    def test_does_not_mutate_the_callers_entry_between_recipients(self):
        before = copy.deepcopy(self.entry)
        self.shared()
        self.assertEqual(self.entry, before)


if __name__ == "__main__":
    unittest.main()
