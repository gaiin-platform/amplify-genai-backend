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


if __name__ == "__main__":
    unittest.main()
