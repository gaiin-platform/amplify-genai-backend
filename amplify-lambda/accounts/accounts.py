# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from collections import Counter
from decimal import Decimal
import boto3
from botocore.exceptions import ClientError
import os
import uuid
from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation
)

setup_validated(rules, get_permission_checker)

from pycommon.logger import getLogger
logger = getLogger("accounts")

NO_EXPECTED_VERSION = object()
ACCOUNTS_CHANGED = {
    "success": False,
    "code": "ACCOUNTS_CHANGED",
    "message": "Billing accounts changed since they were loaded. Reload Accounts and try again.",
}

def convert_decimal_in_dict(obj):
    """Recursively finds Decimal values in dict/list structures and converts them to float"""
    if isinstance(obj, list):
        return [convert_decimal_in_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_decimal_in_dict(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj


def accounts_table():
    return boto3.resource("dynamodb").Table(os.environ["ACCOUNTS_DYNAMO_TABLE"])


def read_accounts_record(table, user):
    item = table.get_item(Key={"user": user}, ConsistentRead=True).get("Item")
    if item is not None and not isinstance(item.get("accounts"), list):
        raise ValueError("Stored accounts must be a list")
    return item


def normalize_account(account):
    limit = account.get("rateLimit") or {}
    rate = limit.get("rate")
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float, Decimal)):
            raise ValueError("The rate must be a number or null.")
        rate = Decimal(str(rate))
        if not rate.is_finite():
            raise ValueError("The rate must be a finite number or null.")
    return {
        **account,
        "rateLimit": {**limit, "period": limit.get("period", "Unlimited"), "rate": rate},
    }


def save_accounts_for_user(user, accounts_list, expected_version=NO_EXPECTED_VERSION):
    accounts = []
    for account in accounts_list:
        try:
            accounts.append(normalize_account(account))
        except ValueError as error:
            return {
                "success": False,
                "code": "INVALID_ACCOUNT_RATE",
                "accountId": account["id"],
                "message": f'Invalid rate limit for account "{account["name"]}". {error}',
            }

    try:
        table = accounts_table()
        current = read_accounts_record(table, user)
        current_version = current.get("version") if current is not None else None

        if expected_version is NO_EXPECTED_VERSION:
            # A legacy save can add or edit accounts, but cannot remove an ID
            # without a version that proves the caller read the list first.
            stored_ids = Counter(account["id"] for account in current["accounts"]) if current else Counter()
            if stored_ids - Counter(account["id"] for account in accounts):
                return ACCOUNTS_CHANGED.copy()
        elif expected_version != current_version:
            return ACCOUNTS_CHANGED.copy()

        if current is None:
            condition = {
                "ConditionExpression": "attribute_not_exists(#user)",
                "ExpressionAttributeNames": {"#user": "user"},
            }
        elif current_version is None:
            # Older records have no revision. Match their list until the first
            # versioned write so concurrent changes still fail the condition.
            condition = {
                "ConditionExpression": "attribute_not_exists(#version) AND #accounts = :accounts",
                "ExpressionAttributeNames": {"#version": "version", "#accounts": "accounts"},
                "ExpressionAttributeValues": {":accounts": current["accounts"]},
            }
        else:
            condition = {
                "ConditionExpression": "#version = :version",
                "ExpressionAttributeNames": {"#version": "version"},
                "ExpressionAttributeValues": {":version": current_version},
            }

        version = uuid.uuid4().hex
        response = table.put_item(
            Item={**(current or {}), "user": user, "accounts": accounts, "version": version},
            **condition,
        )
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            return {"success": True, "message": "Accounts saved successfully", "version": version}
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return ACCOUNTS_CHANGED.copy()
        logger.error("Unable to save accounts for user %s: %s", user, error)
    except Exception as error:
        logger.error("Unable to save accounts for user %s: %s", user, error)
    return {"success": False, "message": "Unable to save billing accounts. Please try again."}


@api_tool(
    path="/state/accounts/get",
    name="getUserAccounts",
    method="GET",
    tags=["accounts"],
    description="""Get a list of the user's accounts that costs are charged to.""",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the accounts retrieval was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "array",
                "description": "Array of user account objects",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for the account",
                        },
                        "name": {
                            "type": "string",
                            "description": "Display name of the account",
                        },
                        "balance": {
                            "type": "number",
                            "description": "Current account balance",
                        },
                        "rateLimit": {
                            "type": "object",
                            "description": "Rate limiting configuration for the account",
                        },
                    },
                },
            },
            "version": {
                "type": ["string", "null"],
                "description": "Version of the loaded account list, or null for an unversioned list",
            },
        },
        "required": ["success", "message"],
    },
)
@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("get")
def get_accounts(event, context, user, name, data):
    try:
        item = read_accounts_record(accounts_table(), user)
    except Exception as error:
        logger.error("Unable to retrieve accounts for user %s: %s", user, error)
        return {"success": False, "message": "Unable to load billing accounts. Please try again."}

    return {
        "success": True,
        "message": "Successfully fetched accounts",
        "data": convert_decimal_in_dict(item["accounts"]) if item else [],
        "version": item.get("version") if item else None,
    }


@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("save")
def save_accounts(event, context, user, name, data):
    data = data["data"]
    return save_accounts_for_user(user, data["accounts"], data.get("expectedVersion", NO_EXPECTED_VERSION))
