"""Offline tests for the change set analysis. Run from the repo root:

    python3 -m unittest discover -s .github/scripts/tests -v

The files in samples/ are hand-written examples in the shape returned by
`aws cloudformation describe-change-set`. They use a fake account ID and stack.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(TESTS_DIR, "samples")
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import analyze_changeset as ac  # noqa: E402

IGNORED = ac.load_ignored_types()


def sample(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as handle:
        return json.load(handle)


def run(name):
    return ac.analyze(sample(name), "amplify-lambda-admin", IGNORED)


class IgnoreList(unittest.TestCase):
    def test_api_gateway_deployment_is_the_documented_exception(self):
        self.assertIn("AWS::ApiGateway::Deployment", IGNORED)


class NoChange(unittest.TestCase):
    def test_empty_change_set_is_a_normal_outcome(self):
        result = run("no-change.json")
        self.assertEqual(result["outcome"], "no_changes")
        self.assertEqual(result["total_changes"], 0)
        self.assertEqual(result["destructive"], [])
        self.assertIn("No changes", ac.render_summary(result))

    def test_alternate_wording_from_cloudformation(self):
        doc = sample("no-change.json")
        doc["StatusReason"] = "No updates are to be performed."
        self.assertEqual(ac.analyze(doc, "x", IGNORED)["outcome"], "no_changes")


class Normal(unittest.TestCase):
    def setUp(self):
        self.result = run("normal.json")

    def test_counts(self):
        self.assertEqual(self.result["outcome"], "changes")
        self.assertEqual(self.result["counts"], {"Add": 3, "Modify": 3, "Remove": 1, "Other": 0})
        self.assertEqual(self.result["total_changes"], 7)

    def test_nothing_is_flagged(self):
        self.assertEqual(self.result["destructive"], [])

    def test_api_gateway_removal_is_ignored_but_reported(self):
        ignored = self.result["ignored_destructive"]
        self.assertEqual([row["resource_type"] for row in ignored], ["AWS::ApiGateway::Deployment"])

    def test_summary_has_table_name_and_link_but_no_caution(self):
        text = ac.render_summary(self.result)
        self.assertIn("| Action | Replacement | Resource type | Logical ID |", text)
        self.assertIn("`gha-1002-1-bbbbbbb`", text)
        self.assertIn("https://us-east-1.console.aws.amazon.com/cloudformation/home", text)
        self.assertIn("amplify-demo-admin-dev", text)
        self.assertNotIn("[!CAUTION]", text)
        self.assertIn("Not flagged", text)


class Remove(unittest.TestCase):
    def setUp(self):
        self.result = run("remove.json")

    def test_removals_are_flagged(self):
        flagged = {(row["logical_id"], row["action"]) for row in self.result["destructive"]}
        self.assertEqual(
            flagged,
            {("AdminConfigurationsTable", "Remove"), ("LegacySyncLambdaFunction", "Remove")},
        )

    def test_caution_is_above_the_table_and_flagged_rows_come_first(self):
        text = ac.render_summary(self.result)
        self.assertLess(text.index("[!CAUTION]"), text.index("| Action |"))
        self.assertIn("2 resource(s) will be DELETED or REPLACED", text)
        rows = [line for line in text.splitlines() if line.startswith("| ") and "Action" not in line]
        self.assertIn("**Remove**", rows[0])
        self.assertIn("**Remove**", rows[1])


class Replacement(unittest.TestCase):
    def setUp(self):
        self.result = run("replacement.json")

    def test_true_and_conditional_replacements_are_flagged(self):
        flagged = {(row["logical_id"], row["replacement"]) for row in self.result["destructive"]}
        self.assertEqual(
            flagged, {("AdminConfigurationsTable", "True"), ("AdminUploadsBucket", "Conditional")}
        )

    def test_in_place_modify_is_not_flagged(self):
        names = [row["logical_id"] for row in self.result["destructive"]]
        self.assertNotIn("GetUserAppConfigsLambdaFunction", names)

    def test_summary_names_the_replacements(self):
        text = ac.render_summary(self.result)
        self.assertIn("`AdminConfigurationsTable` (AWS::DynamoDB::Table): Replace (True)", text)
        self.assertIn("`AdminUploadsBucket` (AWS::S3::Bucket): Replace (Conditional)", text)


class Rules(unittest.TestCase):
    def test_is_destructive(self):
        self.assertTrue(ac.is_destructive({"Action": "Remove"}))
        self.assertTrue(ac.is_destructive({"Action": "Modify", "Replacement": "True"}))
        self.assertTrue(ac.is_destructive({"Action": "Modify", "Replacement": "Conditional"}))
        self.assertFalse(ac.is_destructive({"Action": "Modify", "Replacement": "False"}))
        self.assertFalse(ac.is_destructive({"Action": "Add"}))

    def test_ignore_list_only_hides_listed_types(self):
        doc = sample("remove.json")
        result = ac.analyze(doc, "x", ["AWS::ApiGateway::Deployment", "AWS::Lambda::Function"])
        self.assertEqual([row["logical_id"] for row in result["destructive"]], ["AdminConfigurationsTable"])

    def test_empty_ignore_list_flags_api_gateway_too(self):
        result = ac.analyze(sample("normal.json"), "x", [])
        self.assertEqual(len(result["destructive"]), 1)


class Failed(unittest.TestCase):
    def test_other_failures_are_errors(self):
        result = run("failed.json")
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("No export named", ac.render_summary(result))

    def test_unfinished_change_set_is_not_treated_as_ready(self):
        doc = sample("normal.json")
        doc["Status"] = "CREATE_IN_PROGRESS"
        self.assertEqual(ac.analyze(doc, "x", IGNORED)["outcome"], "failed")


class ConsoleLink(unittest.TestCase):
    def test_link_is_url_encoded(self):
        url = run("normal.json")["console_url"]
        self.assertTrue(url.startswith("https://us-east-1.console.aws.amazon.com/cloudformation/home?region=us-east-1#"))
        self.assertIn("stackId=arn%3Aaws%3Acloudformation%3Aus-east-1%3A123456789012%3Astack%2F", url)
        self.assertIn("changeSetId=arn%3Aaws%3Acloudformation", url)

    def test_missing_ids_give_no_link(self):
        self.assertIsNone(ac.console_url(None, None))


class Overview(unittest.TestCase):
    def test_order_numbering_and_warning(self):
        results = {
            "amplify-lambda": ac.analyze(sample("normal.json"), "amplify-lambda", IGNORED),
            "amplify-assistants": ac.analyze(sample("no-change.json"), "amplify-assistants", IGNORED),
            "amplify-lambda-admin": ac.analyze(sample("remove.json"), "amplify-lambda-admin", IGNORED),
        }
        order = ["amplify-lambda", "amplify-assistants", "amplify-lambda-admin", "embedding"]
        text = ac.render_overview(order, results)
        self.assertIn("[!CAUTION]", text)
        self.assertIn("| 1 | `amplify-lambda` |", text)
        self.assertIn("| 2 | `amplify-lambda-admin` |", text)
        self.assertIn("| | `amplify-assistants` | `amplify-demo-admin-dev` | No changes |", text)
        self.assertIn("| | `embedding` | | Not created", text)
        self.assertLess(text.index("`amplify-lambda` |"), text.index("`amplify-lambda-admin` |"))
        self.assertIn("Nothing has been executed", text)

    def test_all_no_changes(self):
        results = {"embedding": ac.analyze(sample("no-change.json"), "embedding", IGNORED)}
        text = ac.render_overview(["embedding"], results)
        self.assertNotIn("[!CAUTION]", text)
        self.assertIn("No change sets need to be executed", text)


class CommandLine(unittest.TestCase):
    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ac.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_analyze_writes_result_and_appends_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = os.path.join(tmp, "admin.result.json")
            summary_path = os.path.join(tmp, "summary.md")
            with open(summary_path, "w") as handle:
                handle.write("existing\n")
            code, _, _ = self.call([
                "analyze", "--changeset", os.path.join(SAMPLES, "remove.json"),
                "--service", "amplify-lambda-admin",
                "--result-out", result_path, "--summary-out", summary_path,
            ])
            self.assertEqual(code, 0)
            with open(result_path) as handle:
                self.assertEqual(len(json.load(handle)["destructive"]), 2)
            with open(summary_path) as handle:
                text = handle.read()
            self.assertTrue(text.startswith("existing\n"))
            self.assertIn("[!CAUTION]", text)

    def test_no_change_exits_zero_and_failure_exits_one(self):
        base = ["analyze", "--service", "x", "--changeset"]
        self.assertEqual(self.call(base + [os.path.join(SAMPLES, "no-change.json")])[0], 0)
        code, _, err = self.call(base + [os.path.join(SAMPLES, "failed.json")])
        self.assertEqual(code, 1)
        self.assertIn("No export named", err)

    def test_overview_reads_results_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.call([
                "analyze", "--changeset", os.path.join(SAMPLES, "normal.json"),
                "--service", "embedding", "--result-out", os.path.join(tmp, "embedding.result.json"),
            ])
            code, out, _ = self.call([
                "overview", "--results-dir", tmp, "--order", '["amplify-lambda","embedding"]',
            ])
            self.assertEqual(code, 0)
            self.assertIn("| 1 | `embedding` |", out)
            self.assertIn("`amplify-lambda` | | Not created", out)


if __name__ == "__main__":
    unittest.main()
