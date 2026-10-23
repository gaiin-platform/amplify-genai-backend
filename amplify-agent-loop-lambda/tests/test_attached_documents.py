import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).parents[1] / "service" / "attached_documents.py"


def load_module(requests_get):
    """Import the module with project-only dependencies stubbed."""
    logger = Mock()
    logger_module = types.ModuleType("pycommon.logger")
    logger_module.getLogger = lambda _name: logger
    stubs = {
        "pycommon": types.ModuleType("pycommon"),
        "pycommon.logger": logger_module,
        "requests": types.SimpleNamespace(get=requests_get),
    }
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("attached_documents_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def response(payload):
    resp = Mock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def xlsx_payload(rows_by_sheet):
    parts = []
    for sheet, rows in rows_by_sheet.items():
        for row_number, text in enumerate(rows, start=1):
            parts.append({"content": text, "location": {"sheet_name": sheet, "row_number": row_number}})
    return {"name": "book.xlsx", "content": parts}


class FormatPartsTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(Mock())

    def test_adds_a_heading_each_time_the_sheet_changes(self):
        parts = xlsx_payload({"Student Enrollments": ["Person Id,Degree Code", "1,PHD"], "Sheet2": ["x,y"]})["content"]
        text = self.mod.format_parts(parts)
        self.assertEqual(
            text,
            "[Sheet: Student Enrollments]\nPerson Id,Degree Code\n1,PHD\n[Sheet: Sheet2]\nx,y",
        )

    def test_keeps_page_and_slide_context(self):
        self.assertIn("[Page 3]", self.mod.format_parts([{"content": "hello", "location": {"page": 3}}]))
        self.assertIn("[Slide 2]", self.mod.format_parts([{"content": "hello", "location": {"slide": 2}}]))

    def test_skips_empty_and_malformed_parts(self):
        parts = [{"content": "   "}, {"content": None}, "not a dict", {"content": "kept"}]
        self.assertEqual(self.mod.format_parts(parts), "kept")


class FetchExtractedPartsTest(unittest.TestCase):
    def test_reads_nested_content_array(self):
        get = Mock(return_value=response({"name": "a", "content": [{"content": "t"}]}))
        mod = load_module(get)
        self.assertEqual(mod.fetch_extracted_parts("https://signed"), [{"content": "t"}])
        self.assertEqual(get.call_args.kwargs["timeout"], mod.FETCH_TIMEOUT_SECONDS)

    def test_reads_bare_list(self):
        mod = load_module(Mock(return_value=response([{"content": "t"}])))
        self.assertEqual(mod.fetch_extracted_parts("https://signed"), [{"content": "t"}])

    def test_returns_none_on_http_failure_or_bad_shape(self):
        mod = load_module(Mock(side_effect=RuntimeError("403")))
        self.assertIsNone(mod.fetch_extracted_parts("https://signed"))
        mod = load_module(Mock(return_value=response({"unexpected": True})))
        self.assertIsNone(mod.fetch_extracted_parts("https://signed"))


class BuildDocumentBlocksTest(unittest.TestCase):
    def sources(self, **overrides):
        base = {
            "name": "book.xlsx",
            "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "local_path": "/tmp/s/book.xlsx",
            "content_url": "https://signed/book",
        }
        base.update(overrides)
        return base

    def test_inlines_a_spreadsheet_with_its_filename_and_sheet(self):
        get = Mock(return_value=response(xlsx_payload({"Student Enrollments": ["Person Id,Degree Code", "1,PHD"]})))
        mod = load_module(get)
        blocks = mod.build_document_blocks({"id1": self.sources()}, {"id1": 0}, token_budget=1000)
        block = blocks["id1"]
        self.assertIn("BEGIN CONTENT OF book.xlsx", block)
        self.assertIn("[Sheet: Student Enrollments]", block)
        self.assertIn("1,PHD", block)
        self.assertNotIn("Only the first", block)

    def test_skips_sources_without_text_and_media_types(self):
        get = Mock(return_value=response(xlsx_payload({"S": ["a"]})))
        mod = load_module(get)
        blocks = mod.build_document_blocks(
            {
                "no_url": self.sources(content_url=None),
                "image": self.sources(type="image/png"),
            },
            {"no_url": 0, "image": 1},
            token_budget=1000,
        )
        self.assertEqual(blocks, {})
        get.assert_not_called()

    def test_failed_fetch_leaves_no_block_and_does_not_raise(self):
        mod = load_module(Mock(side_effect=RuntimeError("expired url")))
        self.assertEqual(mod.build_document_blocks({"id1": self.sources()}, {"id1": 0}, token_budget=1000), {})

    def test_over_budget_document_is_cut_at_a_line_and_flagged(self):
        rows = ["row %03d %s" % (i, "x" * 40) for i in range(200)]
        mod = load_module(Mock(return_value=response(xlsx_payload({"S": rows}))))
        blocks = mod.build_document_blocks({"id1": self.sources()}, {"id1": 0}, token_budget=100)
        block = blocks["id1"]
        self.assertIn("Only the first", block)
        self.assertIn("partially available", block)
        body = block.split("----- BEGIN CONTENT OF book.xlsx -----\n")[1].split("\n----- END")[0]
        self.assertLessEqual(len(body), 100 * mod.CHARS_PER_TOKEN)
        self.assertTrue(body.splitlines()[-1].startswith("row "))
        self.assertNotIn("row 199", block)

    def test_newest_attachment_gets_the_budget_first(self):
        old_rows = ["old %03d %s" % (i, "x" * 40) for i in range(100)]
        new_rows = ["new %03d %s" % (i, "y" * 40) for i in range(100)]
        payloads = {
            "https://signed/old": response(xlsx_payload({"S": old_rows})),
            "https://signed/new": response(xlsx_payload({"S": new_rows})),
        }
        mod = load_module(Mock(side_effect=lambda url, **_kw: payloads[url]))
        blocks = mod.build_document_blocks(
            {
                "old": self.sources(name="old.xlsx", content_url="https://signed/old"),
                "new": self.sources(name="new.xlsx", content_url="https://signed/new"),
            },
            {"old": 0, "new": 2},
            token_budget=1500,  # each file is ~1,230 tokens: the newest fits, the older one does not
        )
        self.assertIn("new 099", blocks["new"])
        self.assertNotIn("Only the first", blocks["new"])
        self.assertIn("old 000", blocks["old"])
        self.assertIn("Only the first", blocks["old"])
        self.assertNotIn("old 099", blocks["old"])


def pipeline_payload():
    """Same shape as the RAG pipeline's output for Ben's workbook: each tab name is its own
    tiny part, followed by that tab's rows; every part carries the pipeline's token count."""
    def part(n, text, tokens, line):
        return {"content": text, "tokens": tokens, "canSplit": False,
                "location": {"paragraph_number": n, "line_number": line}}
    return {"name": "wb", "totalTokens": 3000, "content": [
        part(1, "Sheet1", 2, 1),
        part(2, "a,b,c,d\n1,2,3,4", 500, 3),
        part(3, "Student Enrollments", 4, 10),
        part(4, "Person Id,Degree Code,Total Cumulative Earned Hours\n1,PHD,72", 2400, 12),
        part(5, "Students with NO Enrollments", 6, 40),
        part(6, "Person Id,Degree Code\n9,MS", 100, 42),
    ]}


class RealWorkbookShapeTest(unittest.TestCase):
    TYPE = "excel spreadsheet"  # what prod reports for Ben's file

    def test_tab_names_become_explicit_sheet_markers(self):
        mod = load_module(Mock())
        text = mod.format_parts(pipeline_payload()["content"], spreadsheet=True)
        self.assertEqual(
            [l for l in text.splitlines() if l.startswith("[Sheet:")],
            ["[Sheet: Sheet1]", "[Sheet: Student Enrollments]", "[Sheet: Students with NO Enrollments]"],
        )
        # Each marker sits directly above its own tab's header row.
        lines = text.splitlines()
        self.assertTrue(lines[lines.index("[Sheet: Student Enrollments]") + 1].startswith("Person Id,Degree Code,Total"))

    def test_single_column_rows_are_not_mistaken_for_tab_names(self):
        mod = load_module(Mock())
        parts = [{"content": "alpha", "tokens": 2}, {"content": "beta", "tokens": 2}, {"content": "gamma", "tokens": 2}]
        text = mod.format_parts(parts, spreadsheet=True)
        self.assertNotIn("[Sheet:", text)
        self.assertEqual(text, "alpha\nbeta\ngamma")

    def test_a_large_part_is_never_a_tab_name(self):
        mod = load_module(Mock())
        parts = [{"content": "Totals", "tokens": 500}, {"content": "a,b\n1,2", "tokens": 5}]
        self.assertNotIn("[Sheet:", mod.format_parts(parts, spreadsheet=True))

    def test_non_spreadsheets_are_not_given_sheet_markers(self):
        mod = load_module(Mock())
        text = mod.format_parts(pipeline_payload()["content"], spreadsheet=False)
        self.assertNotIn("[Sheet:", text)

    def test_spreadsheet_detection_covers_prod_and_dev_type_strings(self):
        mod = load_module(Mock())
        self.assertTrue(mod.is_spreadsheet("x.bin", "excel spreadsheet"))
        self.assertTrue(mod.is_spreadsheet("x", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        self.assertTrue(mod.is_spreadsheet("Report.XLSX", None))
        self.assertFalse(mod.is_spreadsheet("notes.docx", "application/pdf"))

    def test_uses_pipeline_token_counts_not_a_chars_estimate(self):
        payload = pipeline_payload()
        mod = load_module(Mock(return_value=response(payload)))
        text = mod.format_parts(payload["content"], spreadsheet=True)
        self.assertEqual(mod.count_tokens(payload["content"], text), 2 + 500 + 4 + 2400 + 6 + 100)
        self.assertGreater(mod.count_tokens(payload["content"], text), mod.estimate_tokens(text))

    def test_budget_is_enforced_with_the_real_token_density(self):
        payload = pipeline_payload()  # 3,012 pipeline tokens
        mod = load_module(Mock(return_value=response(payload)))
        info = {"name": "wb.xlsx", "type": self.TYPE, "content_url": "https://x"}
        fits = mod.build_document_blocks({"a": info}, {"a": 0}, token_budget=3100)["a"]
        self.assertNotIn("Only the first", fits)
        self.assertIn("[Sheet: Student Enrollments]", fits)
        cut = mod.build_document_blocks({"a": info}, {"a": 0}, token_budget=1000)["a"]
        self.assertIn("Only the first", cut)
        self.assertIn("of ~3012 tokens", cut)

    def test_ben_scenario_prompt_has_the_rows_and_tab_for_the_agent(self):
        payload = pipeline_payload()
        mod = load_module(Mock(return_value=response(payload)))
        info = {"name": "wb.xlsx", "type": self.TYPE, "content_url": "https://x", "local_path": "/tmp/s/wb.xlsx"}
        blocks = mod.build_document_blocks({"id": info}, {"id": 0}, token_budget=60000)
        prompt = [{"role": "user", "content": "audit the attached file\n\nI've attached wb.xlsx ... stored at /tmp/s/wb.xlsx."}]
        final = mod.apply_attachment_text(prompt, {0: blocks["id"]})
        content = final[0]["content"]
        self.assertIn("audit the attached file", content)
        self.assertIn("do not need a tool to open the file", content)
        self.assertIn("[Sheet: Student Enrollments]", content)
        self.assertIn("1,PHD,72", content)
        self.assertEqual(prompt[0]["content"].count("1,PHD,72"), 0)  # original untouched


class ApplyAttachmentTextTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(Mock())

    def test_appends_to_the_right_message_without_mutating_the_original(self):
        prompt = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "audit this"},
        ]
        updated = self.mod.apply_attachment_text(prompt, {2: "\n[doc]"})
        self.assertEqual(updated[2]["content"], "audit this\n[doc]")
        self.assertEqual(updated[0]["content"], "first")
        # The caller's prompt (used for tool selection and saved state) is untouched.
        self.assertEqual(prompt[2]["content"], "audit this")

    def test_ignores_unknown_indexes_and_non_string_content(self):
        prompt = [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
        self.assertEqual(self.mod.apply_attachment_text(prompt, {0: "doc", 5: "doc"}), prompt)

    def test_returns_the_same_prompt_when_nothing_to_add(self):
        prompt = [{"role": "user", "content": "hi"}]
        self.assertIs(self.mod.apply_attachment_text(prompt, {}), prompt)


class TokenBudgetTest(unittest.TestCase):
    def test_env_override_and_bad_values(self):
        mod = load_module(Mock())
        with patch.dict(os.environ, {"AGENT_ATTACHED_DOCS_TOKEN_BUDGET": "1234"}):
            self.assertEqual(mod.get_token_budget(), 1234)
        with patch.dict(os.environ, {"AGENT_ATTACHED_DOCS_TOKEN_BUDGET": "lots"}):
            self.assertEqual(mod.get_token_budget(), mod.DEFAULT_TOKEN_BUDGET)
        with patch.dict(os.environ, {"AGENT_ATTACHED_DOCS_TOKEN_BUDGET": "-5"}):
            self.assertEqual(mod.get_token_budget(), 0)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ATTACHED_DOCS_TOKEN_BUDGET", None)
            self.assertEqual(mod.get_token_budget(), mod.DEFAULT_TOKEN_BUDGET)


if __name__ == "__main__":
    unittest.main()
