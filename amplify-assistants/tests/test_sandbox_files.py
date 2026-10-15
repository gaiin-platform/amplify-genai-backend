import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from codeinterpreter.sandbox_files import changed_sandbox_paths, file_signature


class ChangedSandboxPathsTest(unittest.TestCase):
    def test_new_file_is_reported(self):
        self.assertEqual(changed_sandbox_paths({"a.csv": None}, {"a.csv": None, "deck.pptx": None}), ["deck.pptx"])

    def test_unchanged_file_is_not_reported(self):
        sig = (("size", "10"),)
        self.assertEqual(changed_sandbox_paths({"deck.pptx": sig}, {"deck.pptx": sig}), [])

    def test_overwritten_file_is_reported_when_size_or_mtime_changes(self):
        before = {"deck.pptx": (("size", "10"),)}
        after = {"deck.pptx": (("size", "999"),)}
        self.assertEqual(changed_sandbox_paths(before, after), ["deck.pptx"])

    def test_no_metadata_falls_back_to_path_only(self):
        self.assertEqual(changed_sandbox_paths({"deck.pptx": None}, {"deck.pptx": None}), [])

    def test_file_signature_reads_size_and_time_from_block_or_resource(self):
        block = {"type": "resource_link", "uri": "file:///deck.pptx", "size": 5}
        resource = {"lastModified": "2026-10-13T10:00:00Z"}
        self.assertEqual(file_signature(block, resource), (("lastModified", "2026-10-13T10:00:00Z"), ("size", "5")))
        self.assertIsNone(file_signature({"uri": "x"}, {}))


if __name__ == "__main__":
    unittest.main()
