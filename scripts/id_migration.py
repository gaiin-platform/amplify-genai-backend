# This script is useful in migrating user ID's from one format to another.
# It is especially helpful in the event that, during SSO, the `username` needs
# to be changed.
# One example might be that SSO was initially set up to use email for username
# and then later changed some thing immutable so that email address changes don't
# impact the user.
import sys
import csv
import argparse
import boto3
import json
from datetime import datetime
from typing import Dict, Tuple
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")
tables: Dict[str, str]


def log(*messages):
    for message in messages:
        print(f"[{datetime.now()}]", message)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrate user IDs from one format to another."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not make any changes, just show what would happen.",
    )
    parser.add_argument(
        "--csv-file",
        required=True,
        help="Path to the CSV file containing migration data.",
    )
    parser.add_argument(
        "--log", required=True, help="Log output to the specified file."
    )
    return parser.parse_args()


def get_tables_from_config_file() -> Dict[str, str]:
    """Read the config file and return the table names."""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        return config
    except Exception as e:
        log(f"Error reading config file: {e}")
        sys.exit(1)


def tables_ok(table_names: Dict[str, str]) -> bool:
    """Check if the required tables exist."""
    try:
        existing_tables = dynamodb.meta.client.list_tables()["TableNames"]
        for table in table_names.values():
            if table not in existing_tables:
                log(f"Table {table} does not exist.")
                return False

        # Print the table mapping and ask the user to
        # confirm they have accepted the terms
        log("The following tables will be used:")
        for k, v in table_names.items():
            log(f"\t{k}: {v}")
        response = input("Have you accepted the terms and wish to proceed? (yes/no): ")
        if response.lower() != "yes":
            log("User did not accept the terms. Exiting.")
            return False
        return True

    except Exception as e:
        log(f"Error checking tables: {e}")
        return False


def get_user(old_id: str) -> dict | None:
    """Fetch user by old ID."""
    table = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
    try:
        account = dynamodb.Table(table)
        response = account.query(KeyConditionExpression=Key("user_id").eq(old_id))
        if "Items" in response and response["Items"]:
            return response["Items"][0]
        else:
            return None
    except Exception as e:
        return None


def get_users_from_csv(file_path: str) -> Dict[str, str]:
    """Read CSV and return mapping of old_id to new_id."""
    users = {}
    try:
        with open(file_path, mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                old_id = row.get("old_id")
                new_id = row.get("new_id")
                if old_id and new_id:
                    if new_id in users.values():
                        log(
                            f"Warning: Duplicate new_id {new_id} found. Clean up CSV and try again."
                        )
                        sys.exit(1)
                    users[old_id] = new_id
                else:
                    log(f"Skipping invalid row: {row}")
    except Exception as e:
        log(f"Error reading CSV file {file_path}: {e}")
        sys.exit(1)
    return users


## Starting Here is all the functions that actually update the data ###
## ----------------------------------------------------------------- ##


def update_user_id(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update the user ID of the given user."""
    msg = f"[update_user_id][dry-run: {dry_run}] %s"
    try:
        user = get_user(old_id)
        user_table = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
        # update username
        if not user:
            log(msg % f"User with old ID {old_id} not found.")
            return False
        log(msg % f"Found user with old ID {old_id}.\n\tExisting Data: {user}")
        user["user_id"] = new_id
        if dry_run:
            log(
                msg
                % f"Would update user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            return True
        else:
            # save the user back to the table
            log(
                msg % f"Updating user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            dynamodb.Table(user_table).put_item(Item=user)
            return True
    except Exception as e:
        log(msg % f"Error updating user ID from {old_id} to {new_id}: {e}")
        return False


def update_accounts(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all accounts associated with the old user ID to the new user ID."""
    msg = f"[update_accounts][dry-run: {dry_run}] %s"
    table = table_names.get("ACCOUNTS_DYNAMO_TABLE")
    try:
        account = dynamodb.Table(table)
        # get raw table query by user id
        raw_account = account.query(KeyConditionExpression=Key("user").eq(old_id))
        if "Items" not in raw_account or not raw_account["Items"]:
            log(msg % f"No accounts found for user ID {old_id}.")
            return True  # No accounts to update, so we consider it successful
        log(
            msg
            % f"Found accounts record for user ID {old_id}.\n\tExisting Data: {raw_account['Items']}"
        )
        # create a new copy of the record with the updated username
        for item in raw_account["Items"]:
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update account item to:\n\tNew Data:{item}")
            else:
                log(msg % f"Updating account item to:\n\tNew Data:{item}")
                account.put_item(Item=item)
        return True

    except Exception as e:
        log(msg % f"Error updating accounts for user ID from {old_id} to {new_id}: {e}")
        return False


def update_api_keys(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all API keys associated with the old user ID to the new user ID."""
    # NOTE: This table does not allow us to query by the old user ID, so we
    # have to scan the entire table. This is not efficient, but it is a one-time
    # operation.
    msg = f"[update_api_keys][dry-run: {dry_run}] %s"
    table = table_names.get("API_KEYS_DYNAMODB_TABLE")
    try:
        api_keys_table = dynamodb.Table(table)

        # Get all API key records for the old user ID
        # by finding the 'api_owner_id' field that starts with
        # the old user ID
        # TODO(Karely): Should we search by owner_id instead?
        raw_keys = api_keys_table.scan(
            FilterExpression=Attr("api_owner_id").begins_with(old_id)
        )
        if "Items" not in raw_keys or not raw_keys["Items"]:
            log(msg % f"No API keys found for user ID {old_id}.")
            return True  # No API keys to update, so we consider it successful
        # create a new copy of the record with the updated username
        for item in raw_keys["Items"]:
            log(
                msg
                % f"Found API keys record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["api_owner_id"] = item["api_owner_id"].replace(old_id, new_id)
            # TODO(Karely): Does 'owner' need to reflect the new ID?
            item["owner"] = new_id
            if dry_run:
                log(msg % f"Would update API key item to:\n\tNew Data:{item}")
            else:
                log(msg % f"Updating API key item to:\n\tNew Data:{item}")
                api_keys_table.put_item(Item=item)
        return True

    except Exception as e:
        log(msg % f"Error updating API keys for user ID from {old_id} to {new_id}: {e}")
        return False


def update_ops_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all ops records associated with the old user ID to the new user ID."""
    msg = f"[update_ops_table][dry-run: {dry_run}] %s"
    table = table_names.get("OPS_DYNAMODB_TABLE")
    try:
        ops_table = dynamodb.Table(table)
        # get raw table query by user id
        raw_ops = ops_table.scan(FilterExpression=Attr("user").eq(old_id))
        if "Items" not in raw_ops or not raw_ops["Items"]:
            log(msg % f"No ops records found for user ID {old_id}.")
            return True  # No ops records to update, so we consider it successful
        # create a new copy of the record with the updated username
        for item in raw_ops["Items"]:
            log(
                msg
                % f"Found ops records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update ops item to:\n\tNew Data:{item}")
            else:
                log(msg % f"Updating ops item to:\n\tNew Data:{item}")
                ops_table.put_item(Item=item)
        return True

    except Exception as e:
        log(
            msg
            % f"Error updating ops records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


if __name__ == "__main__":
    args = parse_args()

    global table_names
    table_names = get_tables_from_config_file()

    if not tables_ok(table_names):
        log("User has not accepted the terms. Exiting.")
        sys.exit(1)

    try:
        print("Setting logging to file...")
        logfile = open(args.log, "w")
        sys.stdout = logfile
        sys.stderr = logfile
        log(f"Starting user ID migration. Dry run: {args.dry_run}")

        # loop through our users
        for u in get_users_from_csv(args.csv_file).items():
            log(f"\n\nProcessing user: old: {u[0]} new: {u[1]}")
            old_user_id = u[0]
            new_user_id = u[1]
            # this is a sanity check to make user exists
            user = get_user(old_user_id)

            if not user:
                log(f"\tUser with old ID {old_user_id} not found. Skipping.")
                continue

            if not update_user_id(old_user_id, new_user_id, args.dry_run):
                log(
                    f"Unable to update user ID for {old_user_id}. Skipping - Manual intervention required."
                )
                continue

            if not update_accounts(old_user_id, new_user_id, args.dry_run):
                log(
                    f"Unable to update accounts for {old_user_id}. Skipping - Manual intervention required."
                )
                continue

            if not update_api_keys(old_user_id, new_user_id, args.dry_run):
                log(
                    f"Unable to update API keys for {old_user_id}. This is assumed reasonable as not all users have API keys."
                )

            if not update_ops_table(old_user_id, new_user_id, args.dry_run):
                log(
                    f"Unable to update ops records for {old_user_id}. This is assumed reasonable as not all users have ops records."
                )

    except Exception as e:
        log(f"Error processing users: {e}")
    finally:
        logfile.close()
