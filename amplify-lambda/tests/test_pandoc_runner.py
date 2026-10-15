import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PANDOC = shutil.which("pandoc")
os.environ.setdefault("PANDOC_BIN", PANDOC or "/opt/bin/pandoc")

from converters import pandoc_runner  # noqa: E402

DECK = "---\ntitle: T\nauthor:\n  - A\n---\n\n# Section\n\n## Slide\n- one\n- two\n"
DECK_WITH_BAD_IMAGE = DECK + "\n## Pic\n![chart](https://invalid.example.invalid/x.png)\n"


class StripImagesTests(unittest.TestCase):
    def test_replaces_image_with_alt_text(self):
        self.assertEqual(pandoc_runner.strip_images("a ![chart](http://x/y.png) b"), "a chart b")

    def test_handles_title_and_empty_alt(self):
        self.assertEqual(pandoc_runner.strip_images('![](http://x/y.png "t")'), "")

    def test_leaves_normal_links_alone(self):
        self.assertEqual(pandoc_runner.strip_images("[link](http://x)"), "[link](http://x)")


@unittest.skipUnless(PANDOC, "pandoc is not installed")
class ConvertWithFallbacksTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _convert(self, markdown, fmat="pptx", template=None):
        src = os.path.join(self.dir, "in.md")
        out = os.path.join(self.dir, "out." + fmat)
        with open(src, "w", encoding="utf-8") as f:
            f.write(markdown)
        return pandoc_runner.convert_with_fallbacks(src, out, template), out

    def test_plain_deck(self):
        (ok, info), out = self._convert(DECK)
        self.assertTrue(ok)
        self.assertEqual(info, [])
        self.assertGreater(os.path.getsize(out), 0)

    def test_unreachable_image_does_not_fail_the_export(self):
        (ok, info), _ = self._convert(DECK_WITH_BAD_IMAGE)
        self.assertTrue(ok)
        self.assertEqual(info, ["images"])

    def test_unusable_template_falls_back_to_default(self):
        bad = os.path.join(self.dir, "bad.pptx")
        with open(bad, "wb") as f:
            f.write(b"not a pptx")
        (ok, info), _ = self._convert(DECK, template=bad)
        self.assertTrue(ok)
        self.assertEqual(info, ["template"])

    def test_missing_binary_reports_failure(self):
        original = pandoc_runner.PANDOC_BIN
        pandoc_runner.PANDOC_BIN = "/nonexistent/pandoc"
        self.addCleanup(setattr, pandoc_runner, "PANDOC_BIN", original)
        (ok, info), _ = self._convert(DECK)
        self.assertFalse(ok)
        self.assertIn("could not be started", info)


if __name__ == "__main__":
    unittest.main()
