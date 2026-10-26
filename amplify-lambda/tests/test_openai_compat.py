import importlib.util
import json
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "chat" / "openai_compat.py"
spec = importlib.util.spec_from_file_location("chat_openai_compat", MODULE_PATH)
oc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oc)


def request(**overrides):
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    body.update(overrides)
    return body


class ParseRequestTests(unittest.TestCase):
    def reject(self, body):
        with self.assertRaises(oc.OpenAIRequestError) as ctx:
            oc.parse_chat_completion_request(body)
        self.assertEqual(ctx.exception.status_code, 400)
        return ctx.exception

    def test_minimal_request_gets_amplify_defaults(self):
        parsed = oc.parse_chat_completion_request(request())
        self.assertEqual(parsed["model"], "gpt-4o")
        self.assertEqual(parsed["temperature"], 1.0)
        self.assertEqual(parsed["max_tokens"], oc.DEFAULT_MAX_TOKENS)
        self.assertEqual(parsed["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(parsed["dataSources"], [])

    def test_translates_to_amplify_payload_shape(self):
        parsed = oc.parse_chat_completion_request(request(
            temperature=0.2, max_tokens=50, dataSources=["a@b.edu/1.json"], assistantId="astp/x"))
        payload = oc.to_amplify_payload(parsed)
        self.assertEqual(payload, {
            "model": "gpt-4o", "temperature": 0.2, "max_tokens": 50,
            "dataSources": ["a@b.edu/1.json"],
            "messages": [{"role": "user", "content": "hi"}],
            "options": {"model": {"id": "gpt-4o"}, "assistantId": "astp/x"},
        })

    def test_max_completion_tokens_wins_over_deprecated_max_tokens(self):
        parsed = oc.parse_chat_completion_request(request(max_tokens=10, max_completion_tokens=20))
        self.assertEqual(parsed["max_tokens"], 20)

    def test_developer_role_becomes_system_and_text_parts_are_joined(self):
        parsed = oc.parse_chat_completion_request(request(messages=[
            {"role": "developer", "content": "be brief"},
            {"role": "user", "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]},
            {"role": "assistant", "content": None},
        ]))
        self.assertEqual(parsed["messages"], [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "one\ntwo"},
            {"role": "assistant", "content": ""},
        ])

    def test_library_default_values_are_accepted(self):
        oc.parse_chat_completion_request(request(
            n=1, top_p=1, frequency_penalty=0, presence_penalty=0, stop=None, tools=[],
            logprobs=False, logit_bias={}, response_format={"type": "text"}, stream=False,
            user="u-1", metadata={"a": "b"}, store=False, service_tier="auto",
        ))

    def test_echoed_assistant_message_fields_are_accepted_when_empty(self):
        oc.parse_chat_completion_request(request(messages=[
            {"role": "assistant", "content": "hello", "refusal": None, "annotations": [],
             "audio": None, "function_call": None, "tool_calls": None},
            {"role": "user", "content": "again"},
        ]))

    def test_unsupported_fields_are_rejected_with_param_and_code(self):
        cases = {
            "stream": True, "n": 2, "top_p": 0.5, "frequency_penalty": 0.5, "presence_penalty": -1,
            "stop": ["x"], "seed": 1, "logit_bias": {"1": 2}, "logprobs": True, "top_logprobs": 2,
            "tools": [{"type": "function"}], "tool_choice": "auto", "functions": [{"name": "f"}],
            "response_format": {"type": "json_object"}, "modalities": ["text", "audio"],
            "reasoning_effort": "high", "stream_options": {"include_usage": True},
            "service_tier": "flex", "parallel_tool_calls": True, "prediction": {"type": "content"},
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                err = self.reject(request(**{field: value}))
                self.assertEqual(err.param, field)
                self.assertEqual(err.code, "unsupported_parameter")

    def test_unknown_fields_are_rejected(self):
        err = self.reject(request(access_token="steal", account="x"))
        self.assertEqual(err.code, "unknown_parameter")
        self.assertIn("Unrecognized request argument", err.message)

    def test_tool_and_function_roles_are_rejected(self):
        for role in ("tool", "function"):
            with self.subTest(role=role):
                err = self.reject(request(messages=[{"role": role, "content": "x"}]))
                self.assertEqual(err.param, "messages[0].role")

    def test_non_text_content_parts_are_rejected(self):
        err = self.reject(request(messages=[{"role": "user", "content": [
            {"type": "text", "text": "ok"}, {"type": "image_url", "image_url": {"url": "http://x"}}]}]))
        self.assertEqual(err.param, "messages[0].content[1].type")
        self.assertEqual(err.code, "unsupported_value")

    def test_message_level_unsupported_fields_are_rejected(self):
        for extra in ({"name": "bob"}, {"tool_calls": [{"id": "1"}]}, {"audio": {"id": "a"}},
                      {"refusal": "no"}, {"mystery": 1}):
            with self.subTest(extra=extra):
                self.reject(request(messages=[{"role": "assistant", "content": "x", **extra}]))

    def test_invalid_values_are_rejected(self):
        bad = [
            request(model=""), request(model=None), {"messages": [{"role": "user", "content": "x"}]},
            request(messages=[]), request(messages="hi"), request(messages=["hi"]),
            request(messages=[{"role": "user"}]), request(messages=[{"role": "robot", "content": "x"}]),
            request(messages=[{"role": "user", "content": 5}]),
            request(temperature=3), request(temperature="hot"), request(temperature=True),
            request(max_tokens=0), request(max_tokens=1.5), request(max_tokens=True),
            request(assistantId=""), request(dataSources="x"), request(dataSources=[1]),
            ["not", "an", "object"], None,
        ]
        for body in bad:
            with self.subTest(body=body):
                self.reject(body)

    def test_data_source_objects_are_allowed(self):
        parsed = oc.parse_chat_completion_request(request(dataSources=[{"id": "x", "type": "text/plain"}]))
        self.assertEqual(parsed["dataSources"], [{"id": "x", "type": "text/plain"}])


class ResponseTests(unittest.TestCase):
    def test_chat_completion_shape(self):
        result = oc.build_chat_completion("gpt-4o", "hello", completion_id="chatcmpl-1", created=5)
        self.assertEqual(result["object"], "chat.completion")
        self.assertEqual(result["id"], "chatcmpl-1")
        self.assertEqual(result["created"], 5)
        self.assertEqual(result["model"], "gpt-4o")
        self.assertEqual(result["choices"][0]["message"]["content"], "hello")
        self.assertEqual(result["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("usage", result)
        self.assertTrue(oc.build_chat_completion("m", "x")["id"].startswith("chatcmpl-"))

    def test_model_list_shape_skips_entries_without_ids(self):
        result = oc.build_model_list(
            [{"id": "a", "provider": "Bedrock"}, {"id": "b"}, {"name": "no id"}, "junk"], created=7)
        self.assertEqual(result, {"object": "list", "data": [
            {"id": "a", "object": "model", "created": 7, "owned_by": "Bedrock"},
            {"id": "b", "object": "model", "created": 7, "owned_by": "amplify"},
        ]})

    def test_delta_text_handles_strings_and_structured_deltas(self):
        self.assertEqual(oc.delta_text("abc"), "abc")
        self.assertEqual(oc.delta_text({"delta": {"text": "x"}}), "x")
        self.assertEqual(oc.delta_text({"text": "y"}), "y")
        self.assertEqual(oc.delta_text({"other": 1}), "")
        self.assertEqual(oc.delta_text(None), "")

    def test_rate_limit_notice(self):
        self.assertIsNone(oc.rate_limit_notice([{"state": {"rateLimitNotice": {"reachedLimit": False}}}]))
        self.assertIsNone(oc.rate_limit_notice(None))
        self.assertEqual(oc.rate_limit_notice(
            [{"st": 1}, {"state": {"modelRateLimit": {"reachedLimit": True, "message": "slow down"}}}]),
            "slow down")

    def test_unavailable_model_maps_to_404_model_not_found(self):
        err = oc.upstream_error(Exception("Invalid model: Model 'nope' not available for this user"))
        self.assertEqual((err.status_code, err.code, err.param), (404, "model_not_found", "model"))
        self.assertEqual(err.error_type, "invalid_request_error")
        self.assertEqual(err.message, "Model 'nope' not available for this user")

    def test_other_invalid_model_errors_stay_502(self):
        # e.g. a server-side models config problem must not be blamed on the caller.
        for text in ("Invalid model: Models configuration is missing or invalid",
                     "Invalid model: Model ID is required but not provided"):
            with self.subTest(text=text):
                self.assertEqual(oc.upstream_error(Exception(text)).status_code, 502)

    def test_upstream_error_mapping(self):
        err = oc.upstream_error(Exception("Request failed with status 404: no such model"))
        self.assertEqual((err.status_code, err.message), (404, "no such model"))
        err = oc.upstream_error(Exception("Request failed with status 429: slow"))
        self.assertEqual((err.status_code, err.code), (429, "rate_limit_exceeded"))
        err = oc.upstream_error(Exception("Request failed with status 500: boom"))
        self.assertEqual((err.status_code, err.message), (502, "boom"))
        err = oc.upstream_error(Exception("stream said error"))
        self.assertEqual((err.status_code, err.error_type), (502, "api_error"))


class FinalizeTests(unittest.TestCase):
    def body(self, response):
        return json.loads(response["body"])

    def test_success_passes_through_with_json_content_type(self):
        response = oc.finalize_response({"statusCode": 200, "body": json.dumps({"a": 1})})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "application/json")
        self.assertEqual(self.body(response), {"a": 1})

    def test_handler_raised_errors_keep_details(self):
        err = oc.OpenAIRequestError("bad stream", param="stream", code="unsupported_parameter")
        validated_output = {"statusCode": 400, "body": json.dumps({"error": f"Error: 400 - {err.encoded()}"})}
        response = oc.finalize_response(validated_output)
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(self.body(response)["error"], {
            "message": "bad stream", "type": "invalid_request_error",
            "param": "stream", "code": "unsupported_parameter"})

    def test_plain_validated_errors_are_wrapped(self):
        response = oc.finalize_response(
            {"statusCode": 401, "body": json.dumps({"error": "Error: 401 - User not found."})})
        error = self.body(response)["error"]
        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(error["message"], "User not found.")
        self.assertEqual(error["type"], "authentication_error")
        self.assertEqual(error["code"], "invalid_api_key")

    def test_auth_layer_rate_limit_becomes_429(self):
        response = oc.finalize_response({"statusCode": 401, "body": json.dumps(
            {"error": "Error: 401 - rate limit exceeded ($5.00/Daily)"})})
        self.assertEqual(response["statusCode"], 429)
        self.assertEqual(self.body(response)["error"]["type"], "rate_limit_error")

    def test_unparseable_error_body_still_yields_envelope(self):
        response = oc.finalize_response({"statusCode": 500, "body": "<html>"})
        self.assertEqual(response["statusCode"], 500)
        self.assertIn("message", self.body(response)["error"])

    def test_exception_mapping(self):
        class UnknownApiUserException(Exception):
            pass

        cases = [
            (LookupError("API key not found."), 401, "invalid_api_key"),
            (PermissionError("API key is inactive."), 403, "insufficient_permissions"),
            (UnknownApiUserException("x"), 401, "invalid_api_key"),
            (RuntimeError("secret internals"), 500, "server_error"),
        ]
        for exc, status, code in cases:
            with self.subTest(exc=exc):
                response = oc.exception_response(exc)
                self.assertEqual(response["statusCode"], status)
                self.assertEqual(self.body(response)["error"]["code"], code)
                self.assertNotIn("secret internals", response["body"])


if __name__ == "__main__":
    unittest.main()
