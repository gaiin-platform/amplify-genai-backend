import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "chat" / "system_prompts.py"
spec = importlib.util.spec_from_file_location("chat_system_prompts", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChatSystemPromptTests(unittest.TestCase):
    def test_base_request_and_existing_system_messages_are_ordered_and_deduped(self):
        messages = module.compose_api_chat_messages(
            [
                {"role": "system", "content": "client prompt"},
                {"role": "user", "content": "hello"},
            ],
            request_prompt="client prompt",
            base_prompt="admin base",
        )
        self.assertEqual(messages, [
            {"role": "system", "content": "admin base"},
            {"role": "system", "content": "client prompt"},
            {"role": "user", "content": "hello"},
        ])

    def test_missing_base_uses_builtin(self):
        messages = module.compose_api_chat_messages([{"role": "user", "content": "hi"}])
        self.assertEqual(messages[0]["content"], module.BUILTIN_ORDINARY_CHAT_PROMPT)

    def test_failed_config_read_disables_optional_features(self):
        module.clear_prompt_config_cache()
        with patch.dict(os.environ, {"AMPLIFY_ADMIN_DYNAMODB_TABLE": "admin"}), patch.object(
            module, "_read_config", side_effect=RuntimeError("offline")
        ):
            config = module.get_prompt_config()
        self.assertFalse(config["availability"]["webSearch"])
        self.assertFalse(config["availability"]["artifacts"])
        self.assertFalse(config["availability"]["codeInterpreter"])
        module.clear_prompt_config_cache()

    def test_loaded_config_is_cached_and_empty_prompt_falls_back_at_composition(self):
        module.clear_prompt_config_cache()
        records = {
            "systemPrompts": {"schemaVersion": 1, "prompts": {"ordinaryChat.base": {"version": 1, "text": ""}}},
            "deploymentFeatures": {"schemaVersion": 1, "availability": {"webSearch": False}},
        }
        read = lambda _table, key: records[key]
        first = module.get_prompt_config(table_name="admin", now_fn=lambda: 10, read_config=read)
        second = module.get_prompt_config(table_name="admin", now_fn=lambda: 11, read_config=lambda *_: self.fail("cache miss"))
        self.assertEqual(first, second)
        messages = module.compose_api_chat_messages([{"role": "user", "content": "hi"}], base_prompt=first["prompts"]["ordinaryChat.base"])
        self.assertEqual(messages[0]["content"], module.BUILTIN_ORDINARY_CHAT_PROMPT)
        module.clear_prompt_config_cache()

    def test_runtime_enforces_utf8_bytes_for_ascii_cjk_and_emoji(self):
        module.clear_prompt_config_cache()
        prompts = {
            "ordinaryChat.base": {"version": 1, "text": "界" * 5461 + "a"},
            "webSearch.use": {"version": 1, "text": "界" * 5461 + "aa"},
            "artifacts.generate": {"version": 1, "text": "😀" * 4096},
            "codeInterpreter.use": {"version": 1, "text": "😀" * 4096 + "😀"},
        }
        records = {
            "systemPrompts": {"schemaVersion": 1, "prompts": prompts},
            "deploymentFeatures": {"schemaVersion": 1, "availability": {}},
        }
        config = module.get_prompt_config(table_name="admin", now_fn=lambda: 20, read_config=lambda _table, key: records[key])
        self.assertEqual(len(config["prompts"]["ordinaryChat.base"].encode("utf-8")), 16384)
        self.assertEqual(config["prompts"]["webSearch.use"], "")
        self.assertEqual(len(config["prompts"]["artifacts.generate"].encode("utf-8")), 16384)
        self.assertEqual(config["prompts"]["codeInterpreter.use"], "")
        messages = module.compose_api_chat_messages([{"role": "user", "content": "hi"}], base_prompt=config["prompts"]["webSearch.use"])
        self.assertEqual(messages[0]["content"], module.BUILTIN_ORDINARY_CHAT_PROMPT)
        module.clear_prompt_config_cache()


if __name__ == "__main__":
    unittest.main()
