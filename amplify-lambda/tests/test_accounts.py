import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError
from jsonschema import validate


ROOT = Path(__file__).parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def service():
    def passthrough(*args, **kwargs):
        return lambda fn: fn

    stubs = {
        "boto3": Mock(),
        "pycommon.api.ops": SimpleNamespace(api_tool=passthrough),
        "pycommon.authz": SimpleNamespace(validated=passthrough, setup_validated=Mock()),
        "pycommon.decorators": SimpleNamespace(required_env_vars=passthrough),
        "pycommon.dal.providers.aws.resource_perms": SimpleNamespace(
            DynamoDBOperation=SimpleNamespace(GET_ITEM="get", PUT_ITEM="put")
        ),
        "pycommon.logger": SimpleNamespace(getLogger=Mock(return_value=Mock())),
        "schemata.permissions": SimpleNamespace(get_permission_checker=Mock()),
        "schemata.schema_validation_rules": SimpleNamespace(rules={}),
    }
    with patch.dict(sys.modules, stubs):
        yield load_source("accounts_under_test", "accounts/accounts.py")


@pytest.fixture
def table(service, monkeypatch):
    table = Mock()
    table.get_item.return_value = {}
    table.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    monkeypatch.setattr(service, "accounts_table", lambda: table)
    return table


def account(account_id, **fields):
    return {"id": account_id, "name": f"Account {account_id}", **fields}


def test_read_errors_do_not_look_like_empty_success(service, table):
    table.get_item.side_effect = TimeoutError("DynamoDB timed out")

    result = service.get_accounts(None, None, "user", None, {})

    assert result["success"] is False
    assert "data" not in result
    table.get_item.assert_called_once_with(Key={"user": "user"}, ConsistentRead=True)
    table.put_item.assert_not_called()


def test_missing_item_returns_empty_snapshot(service, table):
    result = service.get_accounts(None, None, "user", None, {})

    assert result["success"] is True
    assert result["data"] == []
    assert result["version"] is None


def test_existing_record_returns_account_list_and_version(service, table):
    table.get_item.return_value = {
        "Item": {
            "user": "user",
            "accounts": [account("one", rateLimit={"period": "Daily", "rate": Decimal("2.50")})],
            "version": "loaded",
        }
    }

    result = service.get_accounts(None, None, "user", None, {})

    assert result["version"] == "loaded"
    assert result["data"][0]["rateLimit"]["rate"] == 2.5


def test_legacy_payload_without_rate_limit_is_valid_and_persisted(service, table):
    schema = load_source("accounts_schema_under_test", "schemata/save_accounts_schema.py")
    payload = {"accounts": [account("one")], "expectedVersion": None}
    validate(payload, schema.save_accounts_schema)

    result = service.save_accounts(None, None, "user", None, {"data": payload})

    assert result["success"] is True
    write = table.put_item.call_args.kwargs
    assert write["ConditionExpression"] == "attribute_not_exists(#user)"
    assert write["Item"]["version"] == result["version"]
    assert write["Item"]["accounts"][0]["rateLimit"] == {"period": "Unlimited", "rate": None}
    assert "rateLimit" not in payload["accounts"][0]


def test_string_rate_has_a_per_account_error_before_writing(service, table):
    schema = load_source("accounts_schema_under_test", "schemata/save_accounts_schema.py")
    payload = {"accounts": [account("bad", rateLimit={"period": "Daily", "rate": "10"})]}
    validate(payload, schema.save_accounts_schema)

    result = service.save_accounts(None, None, "user", None, {"data": payload})

    assert result["success"] is False
    assert result["code"] == "INVALID_ACCOUNT_RATE"
    assert result["accountId"] == "bad"
    assert "Account bad" in result["message"]
    table.put_item.assert_not_called()


def test_stale_snapshot_is_rejected(service, table):
    table.get_item.return_value = {"Item": {"accounts": [account("one")], "version": "current"}}

    result = service.save_accounts_for_user("user", [account("two")], "outdated")

    assert result["code"] == "ACCOUNTS_CHANGED"
    table.put_item.assert_not_called()


def test_matching_version_can_delete_and_compares_version_atomically(service, table):
    table.get_item.return_value = {
        "Item": {"accounts": [account("one"), account("two")], "version": "current"}
    }

    result = service.save_accounts_for_user("user", [account("two")], "current")

    assert result["success"] is True
    assert result["version"] != "current"
    write = table.put_item.call_args.kwargs
    assert write["ExpressionAttributeValues"] == {":version": "current"}
    assert [row["id"] for row in write["Item"]["accounts"]] == ["two"]


def test_a_concurrent_write_during_save_is_reported_as_a_conflict(service, table):
    table.get_item.return_value = {"Item": {"accounts": [account("one")], "version": "current"}}
    table.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
    )

    result = service.save_accounts_for_user("user", [account("one")], "current")

    assert result["code"] == "ACCOUNTS_CHANGED"


def test_legacy_save_cannot_drop_an_account_but_can_add_one(service, table):
    stored = [account("one")]
    table.get_item.return_value = {"Item": {"accounts": stored}}

    result = service.save_accounts_for_user("user", [account("two")])
    assert result["code"] == "ACCOUNTS_CHANGED"
    table.put_item.assert_not_called()

    result = service.save_accounts_for_user("user", [*stored, account("two")])
    assert result["success"] is True
    write = table.put_item.call_args.kwargs
    assert write["ExpressionAttributeValues"] == {":accounts": stored}
    assert write["ConditionExpression"] == "attribute_not_exists(#version) AND #accounts = :accounts"


def test_save_does_not_write_if_its_own_read_fails(service, table):
    table.get_item.side_effect = TimeoutError("DynamoDB timed out")

    result = service.save_accounts_for_user("user", [account("two")], None)

    assert result["success"] is False
    table.put_item.assert_not_called()


def test_legacy_save_cannot_silently_drop_an_alias_with_the_same_id(service, table):
    primary = account("shared")
    alias = {**primary, "name": "Other name"}
    table.get_item.return_value = {"Item": {"accounts": [primary, alias]}}

    result = service.save_accounts_for_user("user", [primary])

    assert result["code"] == "ACCOUNTS_CHANGED"
    table.put_item.assert_not_called()
