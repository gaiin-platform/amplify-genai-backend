import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codeinterpreter.error_detail import NO_DETAIL_MESSAGE, extract_error_detail  # noqa: E402


class ExtractErrorDetailTests(unittest.TestCase):
    def test_prefers_stderr(self):
        result = {"isError": True, "structuredContent": {"stderr": "Traceback...\nValueError: x\n"},
                  "content": [{"type": "text", "text": "other"}]}
        self.assertEqual(extract_error_detail(result), "Traceback...\nValueError: x")

    def test_falls_back_to_text_blocks_when_no_structured_content(self):
        # This is the shape seen in production: isError=True, structuredContent=None.
        result = {"isError": True, "structuredContent": None,
                  "content": [{"type": "text", "text": "SyntaxError: invalid syntax"}]}
        self.assertEqual(extract_error_detail(result), "SyntaxError: invalid syntax")

    def test_blank_stderr_uses_text_blocks(self):
        result = {"isError": True, "structuredContent": {"stderr": "  "},
                  "content": [{"type": "text", "text": "boom"}]}
        self.assertEqual(extract_error_detail(result), "boom")

    def test_uses_stdout_tail_as_last_resort(self):
        result = {"isError": True, "structuredContent": {"stdout": "partial output"}, "content": []}
        self.assertEqual(extract_error_detail(result), "partial output")

    def test_never_returns_an_empty_message(self):
        for result in ({}, {"isError": True}, {"isError": True, "structuredContent": None, "content": []}, None):
            self.assertEqual(extract_error_detail(result), NO_DETAIL_MESSAGE)


if __name__ == "__main__":
    unittest.main()
