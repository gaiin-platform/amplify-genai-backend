"""The admin endpoint that swaps the document ids saved in an assistant (pure parts, read from the source files)."""
import importlib.util
import logging
from pathlib import Path

import pytest
from boto3.dynamodb.conditions import Key

SERVICE = Path(__file__).parents[1] / "service"
OLD, NEW = "global/abc.content.json", "global2/abc.content.json"


def load_swap():
    spec = importlib.util.spec_from_file_location("reference_swap", SERVICE / "reference_swap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.swap_data_source_ids


@pytest.fixture
def swap():
    return load_swap()


@pytest.fixture
def migrate():
    source = (SERVICE / "migrate_references.py").read_text()
    body = source[source.index("def latest_version"):source.index("@required_env_vars")]
    namespace = {"Key": Key, "logger": logging.getLogger("t"), "swap_data_source_ids": load_swap()}
    exec(body, namespace)
    return namespace["migrate_assistant_references"]


def test_the_id_is_swapped_and_everything_else_is_kept(swap):
    sources = [{"id": OLD, "name": "a.docx", "metadata": {"tags": []}}, {"id": "s3://u/x.json", "name": "page"}]
    result, count = swap(sources, {OLD: NEW})
    assert count == 1 and result[0] == {"id": NEW, "name": "a.docx", "metadata": {"tags": []}} and result[1] == sources[1]
    assert sources[0]["id"] == OLD                                   # the input is not changed


def test_a_matching_key_moves_with_the_id_and_an_unrelated_one_does_not(swap):
    assert swap([{"id": OLD, "key": OLD}], {OLD: NEW})[0] == [{"id": NEW, "key": NEW}]
    assert swap([{"id": OLD, "key": "someone/else.json"}], {OLD: NEW})[0] == [{"id": NEW, "key": "someone/else.json"}]


def test_the_same_document_is_never_listed_twice(swap):
    result, count = swap([{"id": OLD}, {"id": NEW}], {OLD: NEW})
    assert [s["id"] for s in result] == [NEW] and count == 1


def test_nothing_to_swap_changes_nothing(swap):
    sources = [{"id": "other"}]
    assert swap(sources, {OLD: NEW}) == (sources, 0)


class Table:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.updates = []
        self.race = False

    def query(self, IndexName, KeyConditionExpression, ScanIndexForward, Limit):
        public = KeyConditionExpression._values[1]
        found = sorted((r for r in self.rows.values() if r["assistantId"] == public), key=lambda r: r["version"], reverse=True)
        return {"Items": [dict(r) for r in found[:Limit]]}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        if self.race:
            raise RuntimeError("An error occurred (ConditionalCheckFailedException)")
        assert self.rows[Key["id"]]["dataSources"] == ExpressionAttributeValues[":old"]
        self.rows[Key["id"]]["dataSources"] = ExpressionAttributeValues[":new"]
        self.updates.append(Key["id"])


def rows():
    return [
        {"id": "ast/1", "assistantId": "astp/x", "version": 1, "dataSources": [{"id": OLD}]},
        {"id": "ast/2", "assistantId": "astp/x", "version": 2, "dataSources": [{"id": OLD, "name": "a"}, {"id": "s3://u/p.json"}]},
    ]


def test_only_the_newest_version_is_changed(migrate):
    table = Table(rows())
    answer = migrate(table, "astp/x", {OLD: NEW}, "admin")
    assert answer["success"] and answer["data"] == {"changed": True, "replaced": 1, "id": "ast/2"}
    assert table.updates == ["ast/2"] and table.rows["ast/1"]["dataSources"] == [{"id": OLD}]
    assert table.rows["ast/2"]["dataSources"] == [{"id": NEW, "name": "a"}, {"id": "s3://u/p.json"}]


def test_swapping_back_restores_the_original_ids(migrate):
    table = Table(rows())
    migrate(table, "astp/x", {OLD: NEW}, "admin")
    migrate(table, "astp/x", {NEW: OLD}, "admin")
    assert table.rows["ast/2"]["dataSources"] == [{"id": OLD, "name": "a"}, {"id": "s3://u/p.json"}]


def test_nothing_is_written_when_there_is_nothing_to_change(migrate):
    table = Table(rows())
    answer = migrate(table, "astp/x", {"global/zzz.content.json": NEW}, "admin")
    assert answer["data"]["changed"] is False and table.updates == []


def test_an_edit_made_in_the_meantime_is_never_overwritten(migrate):
    table = Table(rows())
    table.race = True
    answer = migrate(table, "astp/x", {OLD: NEW}, "admin")
    assert answer["success"] is False and "try again" in answer["message"]


def test_an_unknown_assistant_is_reported(migrate):
    assert migrate(Table(rows()), "astp/none", {OLD: NEW}, "admin")["success"] is False
