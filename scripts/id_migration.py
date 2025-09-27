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

from config import CONFIG as tables

dynamodb = boto3.resource("dynamodb")


def paginated_query(table_name: str, key_name: str, value: str, index_name: str = None):
    """
    Generator for paginated DynamoDB query results.
    Yields items matching Key(key_name).eq(value).
    """
    table = dynamodb.Table(table_name)
    kwargs = {"KeyConditionExpression": Key(key_name).eq(value)}
    if index_name:
        kwargs["IndexName"] = index_name

    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def paginated_scan(
    table_name: str, attr_name: str, value: str, begins_with: bool = False
):
    """
    Generator for paginated DynamoDB scan results.
    Yields items matching Attr(attr_name).eq(value).
    """
    table = dynamodb.Table(table_name)
    if begins_with:
        kwargs = {"FilterExpression": Attr(attr_name).begins_with(value)}
    else:
        kwargs = {"FilterExpression": Attr(attr_name).eq(value)}

    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


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
    """
    Reads the configuration file and returns a dictionary of table names,
    excluding the entry with the key 'needs_edit'.

    Returns:
        Dict[str, str]: A dictionary containing table names from the configuration file,
        with 'needs_edit' removed.
    """
    t = tables.copy()
    del t["needs_edit"]
    return t


def tables_ok(table_names: Dict[str, str], continue_anyway: bool = False) -> bool:
    """
    Checks if the required DynamoDB tables exist and prompts the user for confirmation to proceed.

    Args:
        table_names (Dict[str, str]): A dictionary mapping logical table names to DynamoDB table names.
        continue_anyway (bool, optional): If True, continues execution even if some tables do not exist,
            setting their values to None. Defaults to False.

    Returns:
        bool: True if all required tables exist (or continue_anyway is True) and the user confirms to proceed;
            False otherwise.

    Side Effects:
        - Logs messages about missing tables and table mappings.
        - Prompts the user for confirmation via input.
    """
    try:
        existing_tables = dynamodb.meta.client.list_tables()["TableNames"]
        for table_key, table_value in table_names.items():
            if table_value not in existing_tables:
                if continue_anyway:
                    log(f"Table {table_value} does not exist, but continuing anyway.")
                    table_names[table_key] = None
                    continue
                else:
                    log(f"Table {table_value} does not exist.")
                    return False
        table_names = {k: v for k, v in table_names.items() if v is not None}
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


# "COGNITO_USERS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-cognito-users",
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


# "AMPLIFY_ADMIN_DYNAMODB_TABLE" : "amplify-v6-admin-dev-admin-configs",
def update_amplify_admin_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all admin configs associated with the old user ID to the new user ID."""
    msg = f"[update_amplify_admin_table][dry-run: {dry_run}] %s"
    table = table_names.get("AMPLIFY_ADMIN_DYNAMODB_TABLE")
    amplify_admin_table = dynamodb.Table(table)

    # For the 'admins' config, we could just do this record a single time
    # by using the CSV file and constructing the proper list. This would
    # be the most efficient way to do it - but its a one time shot and so
    # I'm doing it the easy way for now.
    admin_config = amplify_admin_table.get_item(Key={"config_id": "admins"})
    if not "Item" in admin_config:
        log(msg % f"No admin config found in {table}.")
    else:
        admin_data = list(set(admin_config["Item"].get("data", [])))
        if old_id in admin_data:
            log(
                msg
                % f"Found admin config with old ID {old_id}.\n\tExisting Data: {admin_data}"
            )
            new_admin_data = list(
                set([new_id if uid == old_id else uid for uid in admin_data])
            )
            if dry_run:
                log(
                    msg
                    % f"Would update admin config data to:\n\tNew Data: {new_admin_data}"
                )
            else:
                log(
                    msg
                    % f"Updating admin config data to:\n\tNew Data: {new_admin_data}"
                )
                amplify_admin_table.put_item(
                    Item={"config_id": "admins", "data": new_admin_data}
                )

    # A similar dilemma is presented here and I maintain that this table desperately needs a restructure.
    group_config = amplify_admin_table.get_item(Key={"config_id": "amplifyGroups"})
    if not "Item" in group_config:
        log(msg % f"No amplifyGroups config found in {table}.")
    else:
        group_data = group_config["Item"].get("data", {})
        updated = False
        for group_name, group_info in group_data.items():
            members = list(set(group_info.get("members", [])))
            if old_id in members:
                log(
                    msg
                    % f"Found amplifyGroups config with old ID {old_id} in group {group_name}.\n\tExisting Data: {members}"
                )
                new_members = list(
                    set([new_id if uid == old_id else uid for uid in members])
                )
                group_info["members"] = new_members
                updated = True
                if dry_run:
                    log(
                        msg
                        % f"Would update members of group {group_name} to:\n\tNew Data: {new_members}"
                    )
                else:
                    log(
                        msg
                        % f"Updating members of group {group_name} to:\n\tNew Data: {new_members}"
                    )
        if updated and not dry_run:
            amplify_admin_table.put_item(
                Item={"config_id": "amplifyGroups", "data": group_data}
            )

    # And the most complicated of the tables....
    feature_flags_config = amplify_admin_table.get_item(
        Key={"config_id": "featureFlags"}
    )
    if not "Item" in feature_flags_config:
        log(msg % f"No featureFlags config found in {table}.")
    else:
        feature_flags_data = feature_flags_config["Item"].get("data", {})
        updated = False
        for flag_name, flag_info in feature_flags_data.items():
            user_exceptions = list(set(flag_info.get("userExceptions", [])))
            if old_id in user_exceptions:
                log(
                    msg
                    % f"Found featureFlags config with old ID {old_id} in flag {flag_name}.\n\tExisting Data: {user_exceptions}"
                )
                new_user_exceptions = list(
                    set([new_id if uid == old_id else uid for uid in user_exceptions])
                )
                flag_info["userExceptions"] = new_user_exceptions
                updated = True
                if dry_run:
                    log(
                        msg
                        % f"Would update userExceptions of flag {flag_name} to:\n\tNew Data: {new_user_exceptions}"
                    )
                else:
                    log(
                        msg
                        % f"Updating userExceptions of flag {flag_name} to:\n\tNew Data: {new_user_exceptions}"
                    )
        if updated and not dry_run:
            amplify_admin_table.put_item(
                Item={"config_id": "featureFlags", "data": feature_flags_data}
            )


### User object related tables ###
# "ACCOUNTS_DYNAMO_TABLE": "amplify-v6-lambda-dev-accounts",
def update_accounts(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all accounts associated with the old user ID to the new user ID."""
    msg = f"[update_accounts][dry-run: {dry_run}] %s"
    table = table_names.get("ACCOUNTS_DYNAMO_TABLE")
    try:

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found accounts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update account item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating account item to:\n\tNew Data: {item}")
                dynamodb.Table(table).put_item(Item=item)
            ret = True
        return ret

    except Exception as e:
        log(msg % f"Error updating accounts for user ID from {old_id} to {new_id}: {e}")
        return False


# "API_KEYS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-api-keys",
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

        # TODO:  NOTICE SAM
        # 1. "api_owner_id"  Cannot update - this is referenced for cost tracking, agent use, etc.
        # 2. "owner" attribute needs to be updated DONE
        # 3. "delegate" attribute needs to be updated

        ret = False
        for item in paginated_scan(table, "api_owner_id", old_id, begins_with=True):
            log(
                msg
                % f"Found API keys record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["api_owner_id"] = item["api_owner_id"].replace(old_id, new_id)
            # TODO(Karely): Does 'owner' need to reflect the new ID?
            # TODO(Karely): Confirmed we need delegated field updated
            item["owner"] = new_id
            if dry_run:
                log(msg % f"Would update API key item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating API key item to:\n\tNew Data: {item}")
                api_keys_table.put_item(Item=item)
            ret = True
        return ret

    except Exception as e:
        log(msg % f"Error updating API keys for user ID from {old_id} to {new_id}: {e}")
        return False


# "ARTIFACTS_DYNAMODB_TABLE" : "amplify-v6-artifacts-dev-user-artifacts",
# DONE
def update_artifacts_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all artifacts records associated with the old user ID to the new user ID."""
    msg = f"[update_artifacts_table][dry-run: {dry_run}] %s"
    table = table_names.get("ARTIFACTS_DYNAMODB_TABLE")
    artifacts_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user_id", old_id):
            log(
                msg
                % f"Found artifacts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user_id"] = new_id
            if dry_run:
                log(msg % f"Would update artifact item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating artifact item to:\n\tNew Data: {item}")
                artifacts_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg % f"Error updating artifacts for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "OPS_DYNAMODB_TABLE" : "amplify-v6-lambda-ops-dev-ops",
def update_ops_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all ops records associated with the old user ID to the new user ID."""
    msg = f"[update_ops_table][dry-run: {dry_run}] %s"
    table = table_names.get("OPS_DYNAMODB_TABLE")
    try:
        ops_table = dynamodb.Table(table)

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found ops records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update ops item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating ops item to:\n\tNew Data: {item}")
                ops_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating ops records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "SHARES_DYNAMODB_TABLE" : "amplify-v6-lambda-dev",
def update_shares_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all shares records associated with the old user ID to the new user ID."""
    msg = f"[update_shares_table][dry-run: {dry_run}] %s"
    table = table_names.get("SHARES_DYNAMODB_TABLE")
    shares_table = dynamodb.Table(table)

    # TODO:
    # 1. "id"
    # 2. maybe useless attribute "user"
    # 3. "data" attribute -> List <Dict ({ "sharedBy": str (users_ids)})>
    # Opportunity to migrate settings column elsewhere #TODO


# "OBJECT_ACCESS_DYNAMODB_TABLE" : "amplify-v6-object-access-dev-object-access",
def update_object_access_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all object access records associated with the old user ID to the new user ID."""
    msg = f"[update_object_access_table][dry-run: {dry_run}] %s"
    table = table_names.get("OBJECT_ACCESS_DYNAMODB_TABLE")
    object_access_table = dynamodb.Table(table)

    # TODO:
    # "principal_id"


### Assistants Tables ###
# "ASSISTANTS_ALIASES_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-aliases",
def update_assistants_aliases_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistants aliases records associated with the old user ID to the new user ID."""
    msg = f"[update_assistants_aliases_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANTS_ALIASES_DYNAMODB_TABLE")
    assistants_aliases_table = dynamodb.Table(table)

    # TODO:
    # "user"


# "ASSISTANTS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistants",
def update_assistants_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistants records associated with the old user ID to the new user ID."""
    msg = f"[update_assistants_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANTS_DYNAMODB_TABLE")
    assistants_table = dynamodb.Table(table)
    # TODO:
    # 1. "user"


# "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-code-interpreter-assistants",
def update_assistant_code_interpreter_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all assistant code interpreter records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_code_interpreter_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE")
    assistant_code_interpreter_table = dynamodb.Table(table)

    # TODO:
    # 1. "user"


# "ASSISTANT_THREADS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-threads",
def update_assistant_threads_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant threads records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_threads_table][dry-run: {dry-run}] %s"
    table = table_names.get("ASSISTANT_THREADS_DYNAMODB_TABLE")
    assistant_threads_table = dynamodb.Table(table)

    # TODO:
    # 1. "user"


# "ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-thread-runs",
def update_assistant_thread_runs_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant thread runs records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_thread_runs_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE")
    assistant_thread_runs_table = dynamodb.Table(table)

    # TODO:
    # 1. "user"


# "ASSISTANT_GROUPS_DYNAMO_TABLE" : "amplify-v6-object-access-dev-amplify-groups",
def update_assistant_groups_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant groups records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_groups_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_GROUPS_DYNAMO_TABLE")
    assistant_groups_table = dynamodb.Table(table)
    # TODO:
    # 1. "createdBy"
    # 2. "members" -> Dict < str (user_id), str (permission)>


# "ASSISTANT_LOOKUP_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-lookup",
def update_assistant_lookup_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant lookup records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_lookup_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE")
    assistant_lookup_table = dynamodb.Table(table)
    # TODO:
    # 1. "createdBy"
    # 2. "accessTo" -> Dict < "users": List <str (user_id)>>


# "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE" : "amplify-v6-assistants-dev-group-assistant-conversations",
def update_group_assistant_conversations_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all group assistant conversations records associated with the old user ID to the new user ID."""
    msg = f"[update_group_assistant_conversations_table][dry-run: {dry_run}] %s"
    table = table_names.get("GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE")
    group_assistant_conversations_table = dynamodb.Table(table)
    # TODO:
    # 1. "user"


### Data source related tables ###
# "FILES_DYNAMO_TABLE" : "amplify-v6-lambda-dev-user-files",
def update_files_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all files records associated with the old user ID to the new user ID."""
    msg = f"[update_files_table][dry-run: {dry_run}] %s"
    table = table_names.get("FILES_DYNAMO_TABLE")
    files_table = dynamodb.Table(table)
    ### TODO
    # "createdBy"
    # File IDs CAN remain unchanged during migration


# "HASH_FILES_DYNAMO_TABLE" : "amplify-v6-lambda-dev-hash-files",
def update_hash_files_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all hash files records associated with the old user ID to the new user ID."""
    msg = f"[update_hash_files_table][dry-run: {dry_run}] %s"
    table = table_names.get("HASH_FILES_DYNAMO_TABLE")
    hash_files_table = dynamodb.Table(table)
    ### TODO
    # "originalCreator"
    # Hash File IDs CAN remain unchanged during migration


# "EMBEDDING_PROGRESS_TABLE" : "amplify-v6-embedding-dev-embedding-progress",
def update_embedding_progress_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all embedding progress records associated with the old user ID to the new user ID."""
    msg = f"[update_embedding_progress_table][dry-run: {dry_run}] %s"
    table = table_names.get("EMBEDDING_PROGRESS_TABLE")
    embedding_progress_table = dynamodb.Table(table)
    ### TODO
    # "originalCreator"


# "USER_TAGS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-user-tags",
def update_user_tags_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all user tags records associated with the old user ID to the new user ID."""
    msg = f"[update_user_tags_table][dry-run: {dry_run}] %s"
    table = table_names.get("USER_TAGS_DYNAMO_TABLE")
    user_tags_table = dynamodb.Table(table)
    # TODO:
    # 1. "user"


### AGENT LOOP TABLES ###
# "AGENT_STATE_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-states"   *LESS IMPORTANT*
# NOTE Previously named "amplify-v6-agent-loop-dev-agent-state" and
# renamed to "amplify-v6-agent-loop-dev-agent-states"
# Likely no changes needed
def update_agent_state_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all agent state records associated with the old user ID to the new user ID."""
    # TODO(Karely): This table seems to have an S3 bucket associated with it we'll need
    # to coordinate that change as well.
    msg = f"[update_agent_state_table][dry-run: {dry_run}] %s"
    table = table_names.get("AGENT_STATE_DYNAMODB_TABLE")
    try:
        agent_state_table = dynamodb.Table(table)

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found agent state records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update agent state item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating agent state item to:\n\tNew Data: {item}")
                agent_state_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating agent state records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-event-templates",
# DONE
def update_agent_event_templates_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all agent event templates records associated with the old user ID to the new user ID."""
    msg = f"[update_agent_event_templates_table][dry-run: {dry_run}] %s"
    table = table_names.get("AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE")
    agent_event_templates_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found agent event templates record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(
                    msg
                    % f"Would update agent event template item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating agent event template item to:\n\tNew Data: {item}")
                agent_event_templates_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating agent event templates for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "WORKFLOW_TEMPLATES_TABLE" : "amplify-v6-agent-loop-dev-workflow-registry",
# TODO(Karely): Do we need to consider the S3 key here?
def update_workflow_templates_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all workflow templates records associated with the old user ID to the new user ID."""
    msg = f"[update_workflow_templates_table][dry-run: {dry_run}] %s"
    table = table_names.get("WORKFLOW_TEMPLATES_TABLE")
    workflow_templates_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found workflow templates record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(
                    msg % f"Would update workflow template item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating workflow template item to:\n\tNew Data: {item}")
                workflow_templates_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating workflow templates for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "EMAIL_SETTINGS_DYNAMO_TABLE" : "amplify-v6-agent-loop-dev-email-allowed-senders",
def update_email_settings_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all email settings records associated with the old user ID to the new user ID."""
    msg = f"[update_email_settings_table][dry-run: {dry_run}] %s"
    table = table_names.get("EMAIL_SETTINGS_DYNAMO_TABLE")
    email_settings_table = dynamodb.Table(table)
    # TODO:
    # 1. "email"
    # 2. "allowed_senders" -> List <str (users_ids)>


# "SCHEDULED_TASKS_TABLE" : "amplify-v6-agent-loop-dev-scheduled-tasks",
def update_scheduled_tasks_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all scheduled tasks records associated with the old user ID to the new user ID."""
    msg = f"[update_scheduled_tasks_table][dry-run: {dry_run}] %s"
    table = table_names.get("SCHEDULED_TASKS_TABLE")
    scheduled_tasks_table = dynamodb.Table(table)
    # TODO:
    # 1. "user" only


# "DB_CONNECTIONS_TABLE" : "amplify-v6-lambda-dev-db-connections",
def update_db_connections_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all db connections records associated with the old user ID to the new user ID."""
    msg = f"[update_db_connections_table][dry-run: {dry_run}] %s"
    table = table_names.get("DB_CONNECTIONS_TABLE")
    db_connections_table = dynamodb.Table(table)
    # TODO:
    # 1. "user"


### INTEGRATION TABLES ###
# "OAUTH_STATE_TABLE" : "amplify-v6-assistants-api-dev-oauth-state",
def update_oauth_state_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all OAuth state records associated with the old user ID to the new user ID."""
    msg = f"[update_oauth_state_table][dry-run: {dry_run}] %s"
    table = table_names.get("OAUTH_STATE_TABLE")
    try:
        oauth_state_table = dynamodb.Table(table)

        # TODO(Karely): It'd be nice to be able to query this rather than scan.
        # Also, we're creating new records here when, strictly speaking, we could
        # just update the existing ones in place. Still, this is consistent with what
        # we're doing elsewhere. So, we'll need to delete the old records later.

        # SAM dont think we can query it at this time
        ret = False
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found OAuth state records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update OAuth state item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating OAuth state item to:\n\tNew Data: {item}")
                oauth_state_table.put_item(Item=item)
            ret = True
        return ret

    except Exception as e:
        log(
            msg
            % f"Error updating OAuth state records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "OAUTH_USER_TABLE" : "amplify-v6-assistants-api-dev-user-oauth-integrations",
def update_oauth_user_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all OAuth user records associated with the old user ID to the new user ID."""
    msg = f"[update_oauth_user_table][dry-run: {dry_run}] %s"
    table = table_names.get("OAUTH_USER_TABLE")
    oauth_user_table = dynamodb.Table(table)
    # TODO:
    # 1. "user_integration" prefix must be updated


# "DATA_DISCLOSURE_ACCEPTANCE_TABLE" : "amplify-v6-data-disclosure-dev-acceptance",
def update_data_disclosure_acceptance_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all data disclosure acceptance records associated with the old user ID to the new user ID."""
    msg = f"[update_data_disclosure_acceptance_table][dry-run: {dry_run}] %s"
    table = table_names.get("DATA_DISCLOSURE_ACCEPTANCE_TABLE")
    data_disclosure_acceptance_table = dynamodb.Table(table)
    # TODO:
    # 1. "user"


### Cost calculation related tables ###
# "COST_CALCULATIONS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-cost-calculations",
def update_cost_calculations_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all cost calculations records associated with the old user ID to the new user ID."""
    msg = f"[update_cost_calculations_table][dry-run: {dry_run}] %s"
    table = table_names.get("COST_CALCULATIONS_DYNAMO_TABLE")
    cost_calculations_table = dynamodb.Table(table)
    # TODO:
    # 1. "id"


# "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-history-cost-calculations",
def update_history_cost_calculations_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all history cost calculations records associated with the old user ID to the new user ID."""
    msg = f"[update_history_cost_calculations_table][dry-run: {dry_run}] %s"
    table = table_names.get("HISTORY_COST_CALCULATIONS_DYNAMO_TABLE")
    history_cost_calculations_table = dynamodb.Table(table)
    # TODO:
    # 1. "user" prefix must be updated


# "ADDITIONAL_CHARGES_TABLE": "amplify-v6-chat-billing-dev-additional-charges",
def update_additional_charges_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all additional charges records associated with the old user ID to the new user ID."""
    msg = f"[update_additional_charges_table][dry-run: {dry_run}] %s"
    table = table_names.get("ADDITIONAL_CHARGES_TABLE")
    additional_charges_table = dynamodb.Table(table)

    # TODO:
    # 1. "user"


### Chat related tables ###
# "CHAT_USAGE_DYNAMO_TABLE" : "amplify-v6-lambda-dev-chat-usages",
def update_chat_usage_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all chat usage records associated with the old user ID to the new user ID."""
    msg = f"[update_chat_usage_table][dry-run: {dry_run}] %s"
    table = table_names.get("CHAT_USAGE_DYNAMO_TABLE")
    chat_usage_table = dynamodb.Table(table)

    # TODO:
    # 1. "user"


# "CONVERSATION_METADATA_TABLE" : "amplify-v6-lambda-dev-conversation-metadata",
def update_conversation_metadata_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all conversation metadata records associated with the old user ID to the new user ID."""
    msg = f"[update_conversation_metadata_table][dry-run: {dry_run}] %s"
    table = table_names.get("CONVERSATION_METADATA_TABLE")
    conversation_metadata_table = dynamodb.Table(table)

    # TODO:
    # 1. "user_id"


# "USER_STORAGE_TABLE" : "amplify-v6-lambda-basic-ops-dev-user-storage",
def update_user_storage_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all user storage records associated with the old user ID to the new user ID."""
    msg = f"[update_user_storage_table][dry-run: {dry_run}] %s"
    table = table_names.get("USER_STORAGE_TABLE")
    user_storage_table = dynamodb.Table(table)
    # TODO:
    # 1. "PK" prefix must be updated
    # 2. "appId" prefix must be updated


### Unsure if in use tables ###
# "DYNAMO_DYNAMIC_CODE_TABLE" : "amplify-v6-lambda-basic-ops-dev-dynamic-code-entries",
# "JOB_STATUS_TABLE" : "amplify-v6-assistants-api-dev-job-status",
# "USER_RECORDS_DYNAMODB_TABLE_NAME" : "amplify-v6-lambda-basic-ops-dev-work-records",
# "USER_SESSIONS_DYNAMODB_TABLE_NAME" : "amplify-v6-lambda-basic-ops-dev-work-sessions",


### VERY MUCH LESS IMPORTANT TABLES ###
# "AMPLIFY_ADMIN_LOGS_DYNAMODB_TABLE" : "amplify-v6-admin-dev-admin-logs",
# "user" attribute needs to be updated
# "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE" : "amplify-v6-object-access-dev-amplify-group-logs",
# "user" attribute needs to be updated (can query)
# "OP_LOG_DYNAMO_TABLE" : "amplify-v6-assistants-api-dev-op-log",
# "user" attribute needs to be updated
# "REQUEST_STATE_DYNAMO_TABLE" : "amplify-v6-amplify-js-dev-request-state",
# "user" attribute needs to be updated (can query)


if __name__ == "__main__":
    args = parse_args()

    global table_names
    table_names = get_tables_from_config_file()

    if not tables_ok(table_names, continue_anyway=True):
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

            # if not user:
            #     log(f"\tUser with old ID {old_user_id} not found. Skipping.")
            #     continue

            # if not update_user_id(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update user ID for {old_user_id}. Skipping - Manual intervention required."
            #     )
            #     continue

            # if not update_accounts(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update accounts for {old_user_id}. Skipping - Manual intervention required."
            #     )
            #     # continue

            # if not update_api_keys(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update API keys for {old_user_id}. This is assumed reasonable as not all users have API keys."
            #     )

            # if not update_ops_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update ops records for {old_user_id}. This is assumed reasonable as not all users have ops records."
            #     )

            # if not update_agent_state_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update agent state records for {old_user_id}. This is assumed reasonable as not all users have agent state records."
            #     )

            # if not update_oauth_state_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update OAuth state records for {old_user_id}. This is assumed reasonable as not all users have OAuth state records."
            #     )

            # if not update_amplify_admin_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update Amplify Admin records for {old_user_id}. This is assumed reasonable as not all users are admins."
            #     )

            # if not update_artifacts_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update artifacts records for {old_user_id}. This is assumed reasonable as not all users have artifacts."
            #     )

            # if not update_agent_event_templates_table(
            #     old_user_id, new_user_id, args.dry_run
            # ):
            #     log(
            #         f"Unable to update agent event templates records for {old_user_id}. This is assumed reasonable as not all users have agent event templates."
            #     )

            if not update_workflow_templates_table(
                old_user_id, new_user_id, args.dry_run
            ):
                log(
                    f"Unable to update workflow templates records for {old_user_id}. This is assumed reasonable as not all users have workflow templates."
                )

    except Exception as e:
        log(f"Error processing users: {e}")
    finally:
        logfile.close()
