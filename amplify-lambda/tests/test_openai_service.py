import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_chat_forwarding import load_chat_service

OPENAI_SERVICE_PATH = Path(__file__).parents[1] / "chat" / "openai_service.py"


class HTTPException(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


ACCESS = {"value": ["CHAT"]}


def fake_validated(_op):
    """Mimics pycommon.authz.validated: merges auth keys into the body dict, wraps the result."""
    def decorator(fn):
        def wrapper(event, context):
            data = json.loads(event["body"]) if event.get("body") else {}
            data.update(access_token="amp-key", allowed_access=ACCESS["value"], account="acct")
            try:
                result = fn(event, context, "user@example.edu", event["path"], data)
                return {"statusCode": 200, "body": json.dumps(result)}
            except HTTPException as e:
                return {"statusCode": e.status_code,
                        "body": json.dumps({"error": f"Error: {e.status_code} - {e}"})}
        return wrapper
    return decorator


def load_openai_service():
    service, stubs = load_chat_service()
    stubs["pycommon.exceptions"] = types.ModuleType("pycommon.exceptions")
    stubs["pycommon.exceptions"].HTTPException = HTTPException
    stubs["pycommon.authz"].validated = fake_validated
    stubs["pycommon.llm.chat"].chat_streaming = Mock()
    stubs["requests"] = types.SimpleNamespace(get=Mock())
    # load_chat_service()'s patch.dict drops chat.service on exit; keep the instance tests configure.
    stubs["chat.service"] = service
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("chat.openai_service", OPENAI_SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["chat.openai_service"] = module
        spec.loader.exec_module(module)
    return module, service, stubs


def event(body, path="/v1/chat/completions"):
    return {"path": path, "headers": {"Authorization": "Bearer amp-key"},
            "body": body if isinstance(body, str) or body is None else json.dumps(body)}


def feed(*events, meta=()):
    """Make chat_streaming deliver content events then meta events to the handlers."""
    def fake(_url, _token, _payload, on_content, on_meta=lambda _x: None):
        for item in meta:
            on_meta(item)
        for item in events:
            on_content(item)
    return fake


class ChatCompletionsHandlerTests(unittest.TestCase):
    def setUp(self):
        ACCESS["value"] = ["CHAT"]
        self.mod, self.service, self.stubs = load_openai_service()
        self.service.get_data_source_details = Mock(return_value=[])
        self.service.resolve_model_alias = Mock(side_effect=lambda model, _token: {"SIMPLE": "cheap-model"}.get(model, model))
        self.service.validate_assistant_id = Mock(return_value={"success": True})
        self.service.get_prompt_config = Mock(return_value={
            "prompts": {"ordinaryChat.base": "deployment base"},
            "availability": {"webSearch": True, "artifacts": True, "codeInterpreter": True},
        })
        self.chat_streaming = self.stubs["pycommon.llm.chat"].chat_streaming
        self.chat_streaming.side_effect = feed({"d": "Hello "}, {"d": {"delta": {"text": "world"}}}, {"s": "result", "type": "end"})

    def call(self, body, **kwargs):
        response = self.mod.chat_completions(event(body, **kwargs), None)
        return response, json.loads(response["body"])

    def valid(self, **extra):
        return {"model": "SIMPLE", "messages": [{"role": "user", "content": "hi"}], **extra}

    def test_successful_request_returns_openai_completion(self):
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "application/json")
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "cheap-model")
        self.assertEqual(body["choices"][0]["message"], {"role": "assistant", "content": "Hello world", "refusal": None})
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")

    def test_request_is_translated_and_prepared_like_the_amplify_chat_endpoint(self):
        self.call(self.valid(temperature=0.3, max_completion_tokens=99, assistantId="astp/a",
                             dataSources=["x@y.edu/1.json"]))
        url, token, payload = self.chat_streaming.call_args.args[:3]
        self.assertEqual(url, "https://js-router.example/lambda")
        self.assertEqual(token, "amp-key")
        self.assertEqual(payload["model"], "cheap-model")
        self.assertEqual(payload["options"]["model"], {"id": "cheap-model"})
        self.assertEqual(payload["options"]["assistantId"], "astp/a")
        self.assertEqual((payload["temperature"], payload["max_tokens"]), (0.3, 99))
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "deployment base"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "hi"})
        self.service.get_data_source_details.assert_called_once_with(["x@y.edu/1.json"])
        self.service.validate_assistant_id.assert_called_once_with("astp/a", "amp-key")

    def test_deployment_policy_disables_unavailable_features(self):
        self.service.get_prompt_config.return_value["availability"] = {
            "webSearch": False, "artifacts": False, "codeInterpreter": False}
        self.call(self.valid())
        payload = self.chat_streaming.call_args.args[2]
        self.assertFalse(payload["enableWebSearch"])
        self.assertFalse(payload["options"]["artifacts"])

    def test_streaming_and_other_unsupported_parameters_are_rejected_before_any_work(self):
        for extra, param in (({"stream": True}, "stream"), ({"tools": [{"type": "function"}]}, "tools"),
                             ({"stop": ["x"]}, "stop"), ({"n": 2}, "n")):
            with self.subTest(param=param):
                response, body = self.call(self.valid(**extra))
                self.assertEqual(response["statusCode"], 400)
                self.assertEqual(body["error"]["param"], param)
                self.assertEqual(body["error"]["code"], "unsupported_parameter")
                self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.chat_streaming.assert_not_called()

    def test_caller_cannot_inject_auth_fields_through_the_body(self):
        response, body = self.call(self.valid(allowed_access=["FULL_ACCESS"], access_token="x"))
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "unknown_parameter")
        self.chat_streaming.assert_not_called()

    def test_missing_chat_scope_is_403(self):
        ACCESS["value"] = ["DATA_READ"]
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["error"]["code"], "insufficient_permissions")
        self.chat_streaming.assert_not_called()

    def test_invalid_assistant_id_is_400_on_assistant_param(self):
        self.service.validate_assistant_id.return_value = {"success": False}
        response, body = self.call(self.valid(assistantId="astp/bad"))
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["param"], "assistantId")
        self.chat_streaming.assert_not_called()

    def test_unparseable_and_non_object_bodies_are_400(self):
        for raw in ("{not json", "", None, "[1,2]"):
            with self.subTest(raw=raw):
                response, body = self.call(raw)
                self.assertEqual(response["statusCode"], 400)
                self.assertIn("message", body["error"])

    def test_reached_rate_limit_is_429_even_though_pipeline_returns_text(self):
        self.chat_streaming.side_effect = feed(
            {"d": "limit reached"}, meta=[{"s": "meta", "state": {"rateLimitNotice": {"reachedLimit": True, "message": "daily limit"}}}])
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 429)
        self.assertEqual(body["error"]["message"], "daily limit")
        self.assertEqual(body["error"]["type"], "rate_limit_error")

    def test_pipeline_errors_are_not_returned_as_assistant_text(self):
        self.chat_streaming.side_effect = Exception("Request failed with status 500: provider exploded")
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 502)
        self.assertEqual(body["error"]["message"], "provider exploded")
        self.chat_streaming.side_effect = Exception("Invalid model")
        response, _ = self.call(self.valid())
        self.assertEqual(response["statusCode"], 502)

    def test_unavailable_model_is_a_404_not_a_retryable_502(self):
        # Observed on dev: the JS router streams this as an error event over a 200 response.
        self.chat_streaming.side_effect = Exception("Invalid model: Model 'nope' not available for this user")
        response, body = self.call(self.valid(model="nope"))
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["error"], {
            "message": "Model 'nope' not available for this user", "type": "invalid_request_error",
            "param": "model", "code": "model_not_found"})

    def test_pipeline_client_errors_keep_their_status(self):
        self.chat_streaming.side_effect = Exception("Request failed with status 404: model not found")
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["error"]["message"], "model not found")

    def test_empty_model_output_is_502_not_a_blank_success(self):
        self.chat_streaming.side_effect = feed({"d": "  "})
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 502)
        self.assertEqual(body["error"]["code"], "empty_response")

    def test_missing_chat_endpoint_is_503(self):
        self.stubs["pycommon.api.get_endpoint"].get_endpoint.return_value = None
        # openai_service captured the function at import time
        self.mod.get_endpoint = Mock(return_value=None)
        response, body = self.call(self.valid())
        self.assertEqual(response["statusCode"], 503)

    def test_exceptions_reraised_by_the_auth_layer_become_openai_errors(self):
        for exc, status in ((LookupError("API key not found."), 401), (PermissionError("API key is inactive."), 403),
                            (RuntimeError("db password is hunter2"), 500)):
            with self.subTest(exc=exc):
                self.mod._chat_completions = Mock(side_effect=exc)
                response, body = self.call(self.valid())
                self.assertEqual(response["statusCode"], status)
                self.assertNotIn("hunter2", response["body"])
                self.assertIn("error", body)


class ListModelsHandlerTests(unittest.TestCase):
    def setUp(self):
        ACCESS["value"] = ["CHAT"]
        self.mod, _service, self.stubs = load_openai_service()
        self.get = self.stubs["requests"].get
        self.mod.requests = self.stubs["requests"]

    def call(self):
        with patch.dict("os.environ", {"API_BASE_URL": "https://api.example"}):
            response = self.mod.list_models(event(None, path="/v1/models"), None)
        return response, json.loads(response["body"])

    def reply(self, payload, ok=True, status=200):
        return Mock(ok=ok, status_code=status, json=Mock(return_value=payload))

    def test_lists_available_models_in_openai_shape(self):
        self.get.return_value = self.reply({"success": True, "data": {"models": [
            {"id": "gpt-4o", "provider": "Azure"}, {"id": "claude", "provider": "Bedrock"}]}})
        response, body = self.call()
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["object"], "list")
        self.assertEqual([m["id"] for m in body["data"]], ["gpt-4o", "claude"])
        self.assertEqual(body["data"][0]["object"], "model")
        url = self.get.call_args.args[0]
        self.assertEqual(url, "https://api.example/available_models")
        self.assertEqual(self.get.call_args.kwargs["headers"]["Authorization"], "Bearer amp-key")

    def test_upstream_failure_is_502(self):
        for reply in (self.reply({"success": False}), self.reply({}, ok=False, status=500),
                      Mock(ok=True, status_code=200, json=Mock(side_effect=ValueError("bad json")))):
            with self.subTest():
                self.get.return_value = reply
                response, body = self.call()
                self.assertEqual(response["statusCode"], 502)
                self.assertIn("message", body["error"])

    def test_requires_chat_scope(self):
        ACCESS["value"] = ["DATA_READ"]
        response, _ = self.call()
        self.assertEqual(response["statusCode"], 403)
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
