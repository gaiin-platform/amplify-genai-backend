"""
Unit tests for integrations.o365.outlook.list_messages date / focused-inbox filtering.

These tests mock the Microsoft Graph session, so they verify the $filter / params we
BUILD and how we handle error responses. They do not prove Graph accepts the filters;
that still needs a live smoke test.

Run from the amplify-lambda-assistants-api-office365 directory:
    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)


def _install_stubs():
    """Stub out deployment-only dependencies if they aren't importable locally."""
    try:
        import pycommon.logger  # noqa: F401
    except ImportError:
        logger_mod = types.ModuleType("pycommon.logger")
        logger_mod.getLogger = lambda name: mock.MagicMock()
        sys.modules["pycommon"] = types.ModuleType("pycommon")
        sys.modules["pycommon.logger"] = logger_mod

    # oauth and admin_config pull in boto3/AWS; outlook only needs two names from them.
    oauth_mod = types.ModuleType("integrations.oauth")
    oauth_mod.get_ms_graph_session = mock.MagicMock()
    sys.modules["integrations.oauth"] = oauth_mod

    admin_mod = types.ModuleType("integrations.o365.admin_config")
    admin_mod.get_default_timezone_windows = lambda: "UTC"
    sys.modules["integrations.o365.admin_config"] = admin_mod


_install_stubs()
from integrations.o365 import outlook  # noqa: E402


def graph_response(status=200, body=None):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.json.return_value = body if body is not None else {"value": []}
    resp.text = json.dumps(body or {})
    return resp


def graph_error(status, code, message):
    return graph_response(status, {"error": {"code": code, "message": message}})


class ListMessagesTestBase(unittest.TestCase):
    def setUp(self):
        self.session = mock.MagicMock()
        self.session.get.return_value = graph_response()
        patcher = mock.patch.object(outlook, "get_ms_graph_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, **kwargs):
        return outlook.list_messages("test-user", **kwargs)

    @property
    def sent_params(self):
        return self.session.get.call_args.kwargs["params"]

    @property
    def sent_filter(self):
        return self.sent_params.get("$filter")


class BackwardCompatibilityTests(ListMessagesTestBase):
    def test_no_filters_uses_full_param_set(self):
        self.call(top=10, skip=5)
        self.assertNotIn("$filter", self.sent_params)
        self.assertEqual(self.sent_params["$skip"], 5)
        self.assertEqual(self.sent_params["$orderby"], "receivedDateTime desc")
        self.assertIn("$expand", self.sent_params)

    def test_filter_query_only_is_passed_through_unchanged(self):
        raw = "inferenceClassification eq 'focused' and receivedDateTime ge 2026-09-01T00:00:00Z"
        self.call(filter_query=raw, top=25)
        self.assertEqual(self.sent_filter, raw)

    def test_filter_query_only_uses_minimal_params(self):
        self.call(filter_query="isRead eq false", top=25, skip=10)
        self.assertEqual(set(self.sent_params), {"$filter", "$top", "$select"})
        self.assertEqual(self.sent_params["$top"], 25)

    def test_false_and_none_new_params_change_nothing(self):
        self.call(filter_query="isRead eq false", start_date=None, end_date=None, focused_only=False)
        self.assertEqual(self.sent_filter, "isRead eq false")

    def test_empty_string_dates_are_ignored(self):
        self.call(start_date="", end_date="   ")
        self.assertNotIn("$filter", self.sent_params)


class NewParameterTests(ListMessagesTestBase):
    def test_focused_only(self):
        self.call(focused_only=True)
        self.assertEqual(self.sent_filter, "inferenceClassification eq 'focused'")

    def test_start_date_only_covers_start_of_day(self):
        self.call(start_date="2026-09-01")
        self.assertEqual(self.sent_filter, "receivedDateTime ge 2026-09-01T00:00:00Z")

    def test_end_date_only_covers_end_of_day(self):
        self.call(end_date="2026-09-30")
        self.assertEqual(self.sent_filter, "receivedDateTime le 2026-09-30T23:59:59Z")

    def test_all_three_new_params(self):
        self.call(start_date="2026-09-01", end_date="2026-09-30", focused_only=True)
        self.assertEqual(
            self.sent_filter,
            "receivedDateTime ge 2026-09-01T00:00:00Z and "
            "receivedDateTime le 2026-09-30T23:59:59Z and "
            "inferenceClassification eq 'focused'",
        )

    def test_new_params_use_minimal_params(self):
        self.call(focused_only=True, skip=10)
        self.assertEqual(set(self.sent_params), {"$filter", "$top", "$select"})

    def test_datetime_without_timezone_gets_z(self):
        self.call(start_date="2026-09-01T08:30:00")
        self.assertEqual(self.sent_filter, "receivedDateTime ge 2026-09-01T08:30:00Z")

    def test_datetime_with_z_is_unchanged(self):
        self.call(start_date="2026-09-01T08:30:00Z")
        self.assertEqual(self.sent_filter, "receivedDateTime ge 2026-09-01T08:30:00Z")

    def test_datetime_with_offset_is_unchanged(self):
        self.call(start_date="2026-09-01T08:30:00-05:00")
        self.assertEqual(self.sent_filter, "receivedDateTime ge 2026-09-01T08:30:00-05:00")

    def test_datetime_with_positive_offset_is_unchanged(self):
        self.call(end_date="2026-09-01T08:30:00+02:00")
        self.assertEqual(self.sent_filter, "receivedDateTime le 2026-09-01T08:30:00+02:00")


class CombinedFilterTests(ListMessagesTestBase):
    def test_simple_filter_query_is_anded_without_parens(self):
        self.call(filter_query="isRead eq false", start_date="2026-09-01")
        self.assertEqual(
            self.sent_filter, "isRead eq false and receivedDateTime ge 2026-09-01T00:00:00Z"
        )

    def test_filter_query_with_or_is_parenthesized(self):
        self.call(filter_query="isRead eq false or importance eq 'high'", focused_only=True)
        self.assertEqual(
            self.sent_filter,
            "(isRead eq false or importance eq 'high') and inferenceClassification eq 'focused'",
        )

    def test_filter_query_with_and_is_parenthesized(self):
        self.call(filter_query="isRead eq false and hasAttachments eq true", end_date="2026-09-30")
        self.assertEqual(
            self.sent_filter,
            "(isRead eq false and hasAttachments eq true) and receivedDateTime le 2026-09-30T23:59:59Z",
        )

    def test_uppercase_or_is_parenthesized(self):
        self.call(filter_query="isRead eq false OR importance eq 'high'", focused_only=True)
        self.assertTrue(self.sent_filter.startswith("(isRead eq false OR importance eq 'high')"))


class InvalidDateTests(ListMessagesTestBase):
    def assert_rejected_before_graph_call(self, **kwargs):
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(**kwargs)
        self.assertIn("Invalid date format", str(ctx.exception))
        self.session.get.assert_not_called()

    def test_garbage_start_date(self):
        self.assert_rejected_before_graph_call(start_date="not-a-date")

    def test_garbage_end_date(self):
        self.assert_rejected_before_graph_call(end_date="tomorrow")

    def test_impossible_calendar_date(self):
        self.assert_rejected_before_graph_call(start_date="2026-13-45")


class GraphErrorHandlingTests(ListMessagesTestBase):
    COMPLEX = (
        "InefficientFilter",
        "The restriction or sort order is too complex for this operation.",
    )

    def test_complex_error_on_combined_filter_gets_actionable_message(self):
        self.session.get.return_value = graph_error(400, *self.COMPLEX)
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(filter_query="isRead eq false", start_date="2026-09-01")
        msg = str(ctx.exception)
        self.assertIn("too complex for Microsoft Graph", msg)
        self.assertIn("filter_query", msg)

    def test_complex_error_without_filter_query_is_not_rewritten(self):
        self.session.get.return_value = graph_error(400, *self.COMPLEX)
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(start_date="2026-09-01", focused_only=True)
        self.assertNotIn("too complex for Microsoft Graph", str(ctx.exception))
        self.assertIn("Status: 400", str(ctx.exception))

    def test_complex_error_on_filter_query_only_is_not_rewritten(self):
        self.session.get.return_value = graph_error(400, *self.COMPLEX)
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(filter_query="isRead eq false")
        self.assertNotIn("too complex for Microsoft Graph", str(ctx.exception))

    def test_unrelated_400_on_combined_filter_passes_through(self):
        self.session.get.return_value = graph_error(400, "BadRequest", "Something else went wrong.")
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(filter_query="isRead eq false", focused_only=True)
        self.assertIn("Something else went wrong.", str(ctx.exception))
        self.assertNotIn("too complex for Microsoft Graph", str(ctx.exception))

    def test_non_400_error_on_combined_filter_passes_through(self):
        self.session.get.return_value = graph_error(500, "InefficientFilter", "too complex")
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(filter_query="isRead eq false", focused_only=True)
        self.assertNotIn("too complex for Microsoft Graph", str(ctx.exception))

    def test_missing_folder_still_raises_folder_not_found(self):
        self.session.get.return_value = graph_error(404, "ErrorInvalidIdMalformed", "The folder was not found")
        with self.assertRaises(outlook.FolderNotFoundError):
            self.call(folder_id="nope", focused_only=True)

    def test_network_error_is_wrapped(self):
        self.session.get.side_effect = outlook.requests.ConnectionError("boom")
        with self.assertRaises(outlook.OutlookError) as ctx:
            self.call(focused_only=True)
        self.assertIn("Network error", str(ctx.exception))


class ResultFormattingTests(ListMessagesTestBase):
    def test_messages_are_formatted_and_returned(self):
        raw = [{"id": "1", "subject": "hi"}, {"id": "2", "subject": "yo"}]
        self.session.get.return_value = graph_response(200, {"value": raw})
        with mock.patch.object(outlook, "format_message", side_effect=lambda m, **k: {"id": m["id"]}) as fm:
            result = self.call(focused_only=True)
        self.assertEqual(result, [{"id": "1"}, {"id": "2"}])
        self.assertEqual(fm.call_count, 2)


if __name__ == "__main__":
    unittest.main()
