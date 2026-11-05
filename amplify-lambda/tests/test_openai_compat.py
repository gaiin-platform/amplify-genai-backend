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
            "n": 2, "top_p": 0.5, "frequency_penalty": 0.5, "presence_penalty": -1,
            "stop": ["x"], "seed": 1, "logit_bias": {"1": 2}, "logprobs": True, "top_logprobs": 2,
            "tools": [{"type": "function"}], "tool_choice": "auto", "functions": [{"name": "f"}],
            "modalities": ["text", "audio"],
            "service_tier": "flex", "parallel_tool_calls": True, "prediction": {"type": "content"},
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                err = self.reject(request(**{field: value}))
                self.assertEqual(err.param, field)
                self.assertEqual(err.code, "unsupported_parameter")

    def test_all_offending_fields_are_reported_in_one_error(self):
        err = self.reject(request(tools=[{"type": "function"}], seed=1, mystery=1, other=2))
        self.assertEqual(err.param, "tools")
        for name in ("'tools'", "'seed'", "mystery", "other"):
            self.assertIn(name, err.message)
        err = self.reject(request(mystery=1, tools=[{"type": "function"}]))
        self.assertEqual((err.param, err.code), ("mystery", "unknown_parameter"))

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


class ReasoningTests(unittest.TestCase):
    def reasoning(self, **fields):
        return oc.parse_chat_completion_request(request(**fields))["reasoning"]

    def reject(self, **fields):
        with self.assertRaises(oc.OpenAIRequestError) as ctx:
            oc.parse_chat_completion_request(request(**fields))
        self.assertEqual(ctx.exception.status_code, 400)
        return ctx.exception

    def test_no_reasoning_fields_leave_model_default(self):
        self.assertEqual(self.reasoning(), {})
        self.assertEqual(self.reasoning(reasoning_effort=None, enable_thinking=None, thinking=None), {})

    def test_reasoning_effort_maps_to_amplify_levels(self):
        cases = {"none": {"disableReasoning": True}, "minimal": {"reasoningLevel": "low"},
                 "low": {"reasoningLevel": "low"}, "medium": {"reasoningLevel": "medium"},
                 "high": {"reasoningLevel": "high"}, "xhigh": {"reasoningLevel": "high"},
                 "HIGH": {"reasoningLevel": "high"}}
        for effort, expected in cases.items():
            with self.subTest(effort=effort):
                self.assertEqual(self.reasoning(reasoning_effort=effort), expected)

    def test_enable_thinking(self):
        self.assertEqual(self.reasoning(enable_thinking=False), {"disableReasoning": True})
        self.assertEqual(self.reasoning(enable_thinking=True), {})
        self.assertEqual(self.reject(enable_thinking="no").param, "enable_thinking")

    def test_reasoning_object_string_and_bool_forms(self):
        self.assertEqual(self.reasoning(reasoning={"effort": "medium", "summary": "auto"}),
                         {"reasoningLevel": "medium"})
        self.assertEqual(self.reasoning(reasoning={"enabled": False}), {"disableReasoning": True})
        self.assertEqual(self.reasoning(reasoning={"max_tokens": 5000}), {"reasoningLevel": "high"})
        self.assertEqual(self.reasoning(reasoning="low"), {"reasoningLevel": "low"})
        self.assertEqual(self.reasoning(reasoning=False), {"disableReasoning": True})
        self.assertEqual(self.reject(reasoning=5).param, "reasoning")

    def test_anthropic_thinking_forms(self):
        self.assertEqual(self.reasoning(thinking={"type": "disabled"}), {"disableReasoning": True})
        self.assertEqual(self.reasoning(thinking={"type": "enabled", "budget_tokens": 1024}),
                         {"reasoningLevel": "low"})
        self.assertEqual(self.reasoning(thinking={"type": "enabled", "budget_tokens": 3000}),
                         {"reasoningLevel": "medium"})
        self.assertEqual(self.reasoning(thinking={"type": "enabled", "budget_tokens": 9000}),
                         {"reasoningLevel": "high"})
        self.assertEqual(self.reasoning(thinking={"type": "adaptive"}), {})
        self.assertEqual(self.reasoning(thinking={"type": "enabled", "budget_tokens": 0}),
                         {"disableReasoning": True})
        self.assertEqual(self.reject(thinking={"type": "weird"}).param, "thinking.type")
        self.assertEqual(self.reject(thinking={"type": "enabled", "budget_tokens": -1}).param,
                         "thinking.budget_tokens")

    def test_vllm_style_fields(self):
        self.assertEqual(self.reasoning(chat_template_kwargs={"enable_thinking": False, "x": 1}),
                         {"disableReasoning": True})
        self.assertEqual(self.reasoning(thinking_budget=4096), {"reasoningLevel": "high"})
        self.assertEqual(self.reasoning(include_reasoning=False, reasoning_format="parsed"), {})
        self.assertEqual(self.reject(chat_template_kwargs="x").param, "chat_template_kwargs")

    def test_off_wins_over_a_level_and_first_level_wins(self):
        self.assertEqual(self.reasoning(reasoning_effort="high", enable_thinking=False),
                         {"disableReasoning": True})
        self.assertEqual(self.reasoning(reasoning_effort="low", thinking_budget=9000),
                         {"reasoningLevel": "low"})

    def test_invalid_effort_is_rejected_with_valid_values(self):
        err = self.reject(reasoning_effort="extreme")
        self.assertEqual((err.param, err.code), ("reasoning_effort", "invalid_value"))
        self.assertIn("none, minimal, low, medium, high, xhigh", err.message)

    def test_reasoning_options_reach_the_amplify_payload(self):
        parsed = oc.parse_chat_completion_request(request(reasoning_effort="medium", assistantId="astp/x"))
        options = oc.to_amplify_payload(parsed)["options"]
        self.assertEqual(options, {"model": {"id": "gpt-4o"}, "assistantId": "astp/x",
                                   "reasoningLevel": "medium"})


class ClientCompatibilityTests(unittest.TestCase):
    """Requests shaped like real LiteLLM/OpenAI-SDK traffic must be accepted."""

    def test_stream_options_are_ignored(self):
        parsed = oc.parse_chat_completion_request(
            request(stream=True, stream_options={"include_usage": True}))
        self.assertTrue(parsed["stream"])
        self.assertFalse(oc.parse_chat_completion_request(request(stream_options=None))["stream"])

    def test_stream_must_be_boolean(self):
        with self.assertRaises(oc.OpenAIRequestError) as ctx:
            oc.parse_chat_completion_request(request(stream="yes"))
        self.assertEqual(ctx.exception.param, "stream")

    def test_non_openai_sampling_knobs_are_ignored(self):
        oc.parse_chat_completion_request(request(top_k=40, min_p=0.1, repetition_penalty=1.1))

    def test_json_object_response_format_adds_instruction(self):
        parsed = oc.parse_chat_completion_request(request(response_format={"type": "json_object"}))
        self.assertTrue(parsed["json_mode"])
        self.assertEqual(parsed["messages"][-1]["role"], "system")
        self.assertIn("valid JSON", parsed["messages"][-1]["content"])

    def test_json_schema_response_format_embeds_schema(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        parsed = oc.parse_chat_completion_request(request(response_format={
            "type": "json_schema", "json_schema": {"name": "doc", "strict": True, "schema": schema}}))
        instruction = parsed["messages"][-1]["content"]
        self.assertTrue(parsed["json_mode"])
        self.assertIn("'doc'", instruction)
        self.assertIn(json.dumps(schema), instruction)

    def test_text_response_format_is_a_no_op(self):
        parsed = oc.parse_chat_completion_request(request(response_format={"type": "text"}))
        self.assertFalse(parsed["json_mode"])
        self.assertEqual(parsed["messages"], [{"role": "user", "content": "hi"}])

    def test_bad_response_formats_are_rejected(self):
        for value in ("json", {"type": "xml"}, {"type": "json_schema"},
                      {"type": "json_schema", "json_schema": {"schema": "x"}}):
            with self.subTest(value=value):
                with self.assertRaises(oc.OpenAIRequestError):
                    oc.parse_chat_completion_request(request(response_format=value))

    def test_structured_output_cleanup(self):
        n = oc.normalize_structured_output
        self.assertEqual(n('{"a": 1}', True), '{"a": 1}')
        self.assertEqual(n('```json\n{"a": 1}\n```', True), '{"a": 1}')
        self.assertEqual(json.loads(n('Sure! Here it is: {"a": [1, 2]} Hope it helps', True)), {"a": [1, 2]})
        self.assertEqual(n("no json here", True), "no json here")
        self.assertEqual(n("```json\n{}\n```", False), "```json\n{}\n```")

    def test_request_field_names_never_include_values(self):
        self.assertEqual(oc.request_field_names({"b": "secret", "a": 1}), ["a", "b"])
        self.assertEqual(oc.request_field_names("x"), [])


class StreamBodyTests(unittest.TestCase):
    def events(self, body):
        self.assertTrue(body.endswith("data: [DONE]\n\n"))
        return [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: {")]

    def test_stream_body_is_standard_chunks(self):
        events = self.events(oc.build_chat_completion_stream("m", "hello", completion_id="c1", created=5))
        self.assertEqual([e["object"] for e in events], ["chat.completion.chunk"] * 3)
        self.assertEqual(events[0]["choices"][0]["delta"], {"role": "assistant", "content": ""})
        self.assertEqual(events[1]["choices"][0]["delta"], {"content": "hello"})
        self.assertEqual(events[2]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(all(e["id"] == "c1" and e["model"] == "m" and "usage" not in e for e in events))

    def test_finalize_unwraps_sse_marker(self):
        body = oc.build_chat_completion_stream("m", "hi")
        response = oc.finalize_response({"statusCode": 200, "body": json.dumps(oc.sse_result(body))})
        self.assertEqual(response["headers"]["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertEqual(response["body"], body)


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
