import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SERVICE_PATH = Path(__file__).parents[1] / "chat" / "service.py"


def load_chat_service():
    """Import facade code with stubbed project-only dependencies for boundary tests."""
    stubs = {
        "requests": types.SimpleNamespace(post=Mock()),
        "pycommon": types.ModuleType("pycommon"),
        "pycommon.llm": types.ModuleType("pycommon.llm"),
        "pycommon.llm.chat": types.ModuleType("pycommon.llm.chat"),
        "pycommon.api": types.ModuleType("pycommon.api"),
        "pycommon.api.get_endpoint": types.ModuleType("pycommon.api.get_endpoint"),
        "pycommon.api.ops": types.ModuleType("pycommon.api.ops"),
        "pycommon.api.models": types.ModuleType("pycommon.api.models"),
        "pycommon.authz": types.ModuleType("pycommon.authz"),
        "pycommon.const": types.ModuleType("pycommon.const"),
        "pycommon.decorators": types.ModuleType("pycommon.decorators"),
        "pycommon.logger": types.ModuleType("pycommon.logger"),
        "pycommon.dal": types.ModuleType("pycommon.dal"),
        "pycommon.dal.providers": types.ModuleType("pycommon.dal.providers"),
        "pycommon.dal.providers.aws": types.ModuleType("pycommon.dal.providers.aws"),
        "pycommon.dal.providers.aws.resource_perms": types.ModuleType("pycommon.dal.providers.aws.resource_perms"),
        "schemata": types.ModuleType("schemata"),
        "schemata.schema_validation_rules": types.ModuleType("schemata.schema_validation_rules"),
        "schemata.permissions": types.ModuleType("schemata.permissions"),
        "boto3": types.SimpleNamespace(resource=Mock()),
    }
    stubs["pycommon.llm.chat"].chat = Mock(return_value=({"stream": "sse"}, {}))
    stubs["pycommon.api.get_endpoint"].get_endpoint = Mock(return_value="https://js-router.example/lambda")
    stubs["pycommon.api.get_endpoint"].EndpointType = types.SimpleNamespace(CHAT_ENDPOINT="CHAT_ENDPOINT")
    stubs["pycommon.api.ops"].api_tool = lambda **_kwargs: lambda fn: fn
    stubs["pycommon.api.models"].get_default_models = Mock(return_value={"cheapest_model": "cheap", "advanced_model": "strong"})
    stubs["pycommon.authz"].validated = lambda _name: lambda fn: fn
    stubs["pycommon.authz"].setup_validated = Mock()
    stubs["pycommon.authz"].add_api_access_types = Mock()
    stubs["pycommon.authz"].get_permission_checker = Mock()
    stubs["pycommon.const"].APIAccessType = types.SimpleNamespace(CHAT=types.SimpleNamespace(value="CHAT"), FULL_ACCESS=types.SimpleNamespace(value="FULL_ACCESS"))
    stubs["pycommon.decorators"].required_env_vars = lambda _vars: lambda fn: fn
    stubs["pycommon.logger"].getLogger = lambda _name: Mock()
    stubs["pycommon.dal.providers.aws.resource_perms"].DynamoDBOperation = types.SimpleNamespace(BATCH_GET_ITEM="batch")
    stubs["pycommon.dal.providers.aws.resource_perms"].SecretsManagerOperation = types.SimpleNamespace(GET_SECRET_VALUE="secret")
    stubs["schemata.schema_validation_rules"].rules = {}
    stubs["schemata.permissions"].get_permission_checker = Mock()
    parent = types.ModuleType("chat")
    parent.__path__ = [str(SERVICE_PATH.parent)]
    sys.modules["chat"] = parent
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("chat.service", SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["chat.service"] = module
        spec.loader.exec_module(module)
    return module, stubs


class ChatFacadeForwardingTests(unittest.TestCase):
    def setUp(self):
        self.service, self.stubs = load_chat_service()
        self.service.get_data_source_details = Mock(return_value=[])
        self.service.resolve_model_alias = Mock(return_value="model-allowed-by-downstream")
        self.service.validate_assistant_id = Mock(return_value={"success": True})
        self.service.get_prompt_config = Mock(return_value={
            "prompts": {"ordinaryChat.base": "deployment base"},
            "availability": {"webSearch": True, "artifacts": True, "codeInterpreter": True},
        })

    def invoke(self, access=("CHAT",), options=None):
        payload = {
            "messages": [{"role": "user", "content": "Find current information"}],
            "dataSources": [],
            "options": {"model": {"id": "SIMPLE"}, **(options or {})},
        }
        result = self.service.chat_endpoint(None, None, {}, "test", {
            "access_token": "token", "allowed_access": list(access), "data": payload,
        })
        return result, payload

    def test_facade_forwards_to_single_configured_router_with_policy_and_stream_result(self):
        response, payload = self.invoke(options={"assistantId": "astp/selected", "groupId": "grp/1"})
        self.assertTrue(response["success"])
        self.assertEqual(response["data"], {"stream": "sse"})
        endpoint, _token, forwarded = self.stubs["pycommon.llm.chat"].chat.call_args.args
        self.assertEqual(endpoint, "https://js-router.example/lambda")
        self.assertEqual(forwarded["options"]["assistantId"], "astp/selected")
        self.assertEqual(forwarded["options"]["groupId"], "grp/1")
        self.assertEqual(forwarded["model"], "model-allowed-by-downstream")
        self.assertEqual(forwarded["messages"][0], {"role": "system", "content": "deployment base"})
        self.assertEqual(payload["messages"][1]["content"], "Find current information")

    def test_deployment_disabled_features_strip_explicit_client_intent(self):
        self.service.get_prompt_config.return_value["availability"] = {
            "webSearch": False, "artifacts": False, "codeInterpreter": False,
        }
        _, payload = self.invoke(options={
            "artifacts": True, "artifactsMode": True, "codeInterpreterOnly": True,
            "enableWebSearch": True,
        })
        self.assertFalse(payload["enableWebSearch"])
        self.assertFalse(payload["options"]["enableWebSearch"])
        self.assertFalse(payload["options"]["artifacts"])
        self.assertFalse(payload["options"]["artifactsMode"])
        self.assertFalse(payload["options"]["codeInterpreterOnly"])

    def test_api_authorization_still_rejects_missing_chat_scope(self):
        response, _ = self.invoke(access=("DATA_READ",))
        self.assertFalse(response["success"])
        self.stubs["pycommon.llm.chat"].chat.assert_not_called()


class ChatServiceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.service, self.stubs = load_chat_service()
        self.service.resolve_model_alias = Mock(return_value="m")
        self.service.validate_assistant_id = Mock(return_value={"success": True})
        self.service.get_prompt_config = Mock(return_value={"prompts": {}, "availability": {}})

    def invoke(self, payload):
        return self.service.chat_endpoint(None, None, {}, "test", {
            "access_token": "token", "allowed_access": ["CHAT"], "data": payload})

    def test_payload_without_data_sources_is_accepted(self):
        # dataSources is not required by the /chat schema; it used to raise KeyError.
        result = self.invoke({"messages": [{"role": "user", "content": "hi"}], "options": {"model": {"id": "x"}}})
        self.assertTrue(result["success"])
        self.assertEqual(self.stubs["pycommon.llm.chat"].chat.call_args.args[2]["dataSources"], [])

    def test_failures_return_a_json_serializable_message(self):
        # The message used to be a set literal, which json.dumps cannot serialize.
        self.stubs["pycommon.llm.chat"].chat.side_effect = RuntimeError("boom")
        result = self.invoke({"messages": [], "dataSources": [], "options": {"model": {"id": "x"}}})
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Chat service error: boom")
        import json
        json.dumps(result)

    def test_invalid_assistant_id_is_reported_without_calling_the_model(self):
        self.service.validate_assistant_id.return_value = {"success": False}
        result = self.invoke({"messages": [], "dataSources": [], "options": {"model": {"id": "x"}, "assistantId": "astp/x"}})
        self.assertEqual(result, {"success": False, "message": "Invalid assistant id"})
        self.stubs["pycommon.llm.chat"].chat.assert_not_called()

    def test_unresolvable_alias_is_returned_unchanged(self):
        service, stubs = load_chat_service()
        stubs["pycommon.api.models"].get_default_models.return_value = {}
        service.get_default_models = Mock(return_value={})
        self.assertEqual(service.resolve_model_alias("SIMPLE", "t"), "SIMPLE")
        service.get_default_models = Mock(return_value={"cheapest_model": "c", "advanced_model": "a"})
        self.assertEqual(service.resolve_model_alias("COMPLEX", "t"), "a")
        self.assertEqual(service.resolve_model_alias("gpt", "t"), "gpt")


class DataSourceLookupTests(unittest.TestCase):
    def setUp(self):
        self.service, _ = load_chat_service()
        self.table = Mock()
        self.dynamodb = Mock()
        self.service.boto3.resource.return_value = self.dynamodb
        self.env = patch.dict("os.environ", {"FILES_DYNAMO_TABLE": "files"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def respond(self, ids):
        self.dynamodb.batch_get_item.side_effect = lambda RequestItems: {
            "Responses": {"files": [{"id": i["id"], "name": i["id"]} for i in RequestItems["files"]["Keys"]][::-1]}}

    def test_only_bedrock_sources_skip_dynamodb(self):
        # batch_get_item raises on an empty key list.
        result = self.service.get_data_source_details(["bedrock-kb://kb1"])
        self.assertEqual(result, [{"id": "bedrock-kb://kb1", "type": "bedrock/knowledge-base"}])
        self.dynamodb.batch_get_item.assert_not_called()

    def test_duplicate_ids_are_looked_up_once_and_order_is_preserved(self):
        self.respond(None)
        result = self.service.get_data_source_details(["b", "a", "s3://b"])
        keys = self.dynamodb.batch_get_item.call_args.kwargs["RequestItems"]["files"]["Keys"]
        self.assertEqual(keys, [{"id": "b"}, {"id": "a"}])
        self.assertEqual([item["key"] for item in result], ["b", "a"])

    def test_more_than_100_ids_are_split_across_requests(self):
        self.respond(None)
        ids = [f"id-{n}" for n in range(250)]
        result = self.service.get_data_source_details(ids)
        sizes = [len(c.kwargs["RequestItems"]["files"]["Keys"]) for c in self.dynamodb.batch_get_item.call_args_list]
        self.assertEqual(sizes, [100, 100, 50])
        self.assertEqual([item["key"] for item in result], ids)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(self.service.get_data_source_details([]), [])


if __name__ == "__main__":
    unittest.main()
