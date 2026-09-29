import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).parents[1] / "service" / "system_prompt_validation.py"
spec = importlib.util.spec_from_file_location("system_prompt_validation", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SystemPromptValidationTests(unittest.TestCase):
    def record(self, text):
        return {"schemaVersion": 1, "prompts": {"amplifyHelper.base": {"version": 1, "text": text}}}

    def test_accepts_exact_ascii_and_unicode_byte_boundaries(self):
        for text in ("a" * 16384, "界" * 5461 + "a", "😀" * 4096):
            self.assertEqual(len(text.encode("utf-8")), 16384)
            self.assertIsNone(module.validate_system_prompts(self.record(text)))

    def test_rejects_multibyte_over_limit_including_emoji(self):
        for text in ("界" * 5461 + "aa", "😀" * 4096 + "a"):
            error = module.validate_system_prompts(self.record(text))
            self.assertIn("UTF-8 bytes", error)
            self.assertIn("amplifyHelper.base", error)

    def test_rejection_happens_before_persistence(self):
        writes = []
        result = module.update_system_prompts(self.record("😀" * 4097), lambda data: writes.append(data) or {"success": True})
        self.assertFalse(result["success"])
        self.assertEqual(writes, [])

    def test_valid_prompt_is_persisted(self):
        writes = []
        payload = self.record("valid prompt")
        result = module.update_system_prompts(payload, lambda data: writes.append(data) or {"success": True})
        self.assertTrue(result["success"])
        self.assertEqual(writes, [payload])

    def test_rejects_malformed_surrogates_with_clear_message(self):
        error = module.validate_system_prompts(self.record("\ud800"))
        self.assertIn("unpaired surrogate", error)


if __name__ == "__main__":
    unittest.main()
