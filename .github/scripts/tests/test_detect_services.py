"""Offline tests for change detection. Run from the repo root:

    python3 -m unittest discover -s .github/scripts/tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
sys.path.insert(0, SCRIPTS_DIR)

import detect_services as ds  # noqa: E402

CONFIG = ds.load_config()
ALL = CONFIG["services"]


def select(*paths):
    return ds.select_from_changes(list(paths), CONFIG)[0]


class SingleAndMultiService(unittest.TestCase):
    def test_single_service(self):
        self.assertEqual(select("amplify-lambda-admin/service/core.py"), ["amplify-lambda-admin"])

    def test_similar_prefixes_do_not_bleed(self):
        # amplify-lambda is a prefix of several other folder names
        self.assertEqual(select("amplify-lambda-js/index.js"), ["amplify-lambda-js"])
        self.assertEqual(
            select("amplify-lambda-assistants-api-google/x.py"),
            ["amplify-lambda-assistants-api-google"],
        )

    def test_multiple_services_come_back_in_dependency_order(self):
        result = select(
            "object-access/a.py",
            "amplify-lambda-js/b.js",
            "amplify-assistants/c.py",
            "amplify-lambda/d.py",
        )
        self.assertEqual(
            result, ["amplify-lambda", "amplify-assistants", "amplify-lambda-js", "object-access"]
        )

    def test_dependency_order_in_config(self):
        self.assertEqual(ALL[0], "amplify-lambda")
        self.assertLess(ALL.index("amplify-assistants"), ALL.index("amplify-lambda-js"))

    def test_serverless_yml_change_selects_service(self):
        self.assertEqual(select("chat-billing/serverless.yml"), ["chat-billing"])


class SharedPaths(unittest.TestCase):
    def test_shared_paths_select_everything(self):
        for path in (
            "lambda_layers/python/thing.py",
            "npm-shims/decompress/index.js",
            "package.json",
            "package-lock.json",
            "serverless-compose.yml",
        ):
            self.assertEqual(select(path), ALL, path)

    def test_package_json_inside_a_service_is_not_shared(self):
        self.assertEqual(select("amplify-lambda-js/package.json"), ["amplify-lambda-js"])


class NothingToDeploy(unittest.TestCase):
    def test_docs_only(self):
        self.assertEqual(select("README.md", "CHANGES.md", "docs/guide.txt"), [])

    def test_markdown_inside_a_service(self):
        self.assertEqual(select("amplify-lambda-js/README.md"), [])

    def test_docs_folder_inside_a_service(self):
        self.assertEqual(select("amplify-agent-loop-lambda/docs/diagram.png"), [])

    def test_markdown_inside_a_shared_path(self):
        self.assertEqual(select("npm-shims/README.md"), [])

    def test_workflow_only(self):
        self.assertEqual(
            select(".github/workflows/deploy-dev.yml", ".github/scripts/deploy-config.json"), []
        )

    def test_template_folder_is_never_selected(self):
        self.assertEqual(select("amplify-lambda-python-base/handler.py"), [])

    def test_unrelated_root_files(self):
        self.assertEqual(select("build-layer.sh", "scripts/tool.py", ".gitignore"), [])

    def test_empty_diff(self):
        self.assertEqual(select(), [])

    def test_docs_plus_code_still_deploys_the_code(self):
        self.assertEqual(select("README.md", "embedding/embedding.py"), ["embedding"])

    def test_deep_docs_folder_in_code_is_not_ignored(self):
        # only <service>/docs/** is treated as documentation
        self.assertEqual(select("embedding/shared/docs/loader.py"), ["embedding"])


class ManualInput(unittest.TestCase):
    def test_all(self):
        self.assertEqual(ds.select_from_input("all", CONFIG), ALL)
        self.assertEqual(ds.select_from_input(" ALL ", CONFIG), ALL)

    def test_list_is_reordered_and_trimmed(self):
        self.assertEqual(
            ds.select_from_input("embedding, amplify-lambda", CONFIG), ["amplify-lambda", "embedding"]
        )

    def test_unknown_service_is_an_error(self):
        with self.assertRaises(ValueError):
            ds.select_from_input("amplify-lambda,not-a-service", CONFIG)

    def test_template_cannot_be_selected(self):
        with self.assertRaises(ValueError):
            ds.select_from_input("amplify-lambda-python-base", CONFIG)

    def test_empty_is_an_error(self):
        with self.assertRaises(ValueError):
            ds.select_from_input(" , ", CONFIG)


class Parsing(unittest.TestCase):
    def test_newline_and_nul_separated(self):
        self.assertEqual(ds.parse_changed_files("a/b.py\nc/d.py\n"), ["a/b.py", "c/d.py"])
        self.assertEqual(ds.parse_changed_files("a/b c.py\0c/d.py\0"), ["a/b c.py", "c/d.py"])


class ConfigMatchesRepo(unittest.TestCase):
    def test_every_service_folder_is_registered(self):
        self.assertEqual(ds.find_unregistered(REPO_ROOT, CONFIG), [])
        self.assertEqual(ds.find_missing(REPO_ROOT, CONFIG), [])

    def test_unregistered_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ALL + ["brand-new-service"]:
                os.makedirs(os.path.join(root, name))
                open(os.path.join(root, name, "serverless.yml"), "w").close()
            self.assertEqual(ds.find_unregistered(root, CONFIG), ["brand-new-service"])
            self.assertEqual(ds.find_missing(root, CONFIG), [])


class CommandLine(unittest.TestCase):
    def run_main(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ds.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue(), err.getvalue()

    def test_stdin_to_json(self):
        code, out, _ = self.run_main(["--changed-files", "-"], "amplify-lambda-admin/x.py\n")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), ["amplify-lambda-admin"])

    def test_docs_only_prints_empty_list(self):
        code, out, _ = self.run_main(["--changed-files", "-"], "README.md\n")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "[]")

    def test_bad_manual_input_fails(self):
        code, out, err = self.run_main(["--services", "nope"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("unknown service", err)


if __name__ == "__main__":
    unittest.main()
