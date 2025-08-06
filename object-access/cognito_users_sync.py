import boto3
from datetime import datetime, timezone
import os


def sync_users_to_dynamo(event, context):
    cognito = boto3.client("cognito-idp")
    dynamodb = boto3.resource("dynamodb")
    user_pool_id = os.environ["COGNITO_USER_POOL_ID"]
    dynamo_table_name = os.environ["COGNITO_USERS_DYNAMODB_TABLE"]

    dynamo_table = dynamodb.Table(dynamo_table_name)
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])

    pagination_token = None

    group_members = {}
    while True:
        args = {"UserPoolId": user_pool_id}
        if pagination_token:
            args["PaginationToken"] = pagination_token

        response = cognito.list_users(**args)

        for user in response["Users"]:
            user_attributes = {
                attr["Name"]: attr["Value"] for attr in user["Attributes"]
            }
            user_id = user_attributes.get("email")  # Use email as the user_id

            if user_id:  # Ensure that user_id (email) is not None
                group_str = user_attributes.get("custom:saml_groups")
                groups = parse_group_string(group_str) if group_str else []

                for g in groups:
                    if g not in group_members:
                        group_members[g] = [user_id]
                    elif user_id not in group_members[g]:
                        group_members[g].append(user_id)

                filtered_attributes = {
                    "user_id": user_id,
                    "family_name": user_attributes.get("family_name"),
                    "given_name": user_attributes.get("given_name"),
                    "custom:saml_groups": group_str,
                    "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                }

                existing_user = dynamo_table.get_item(Key={"user_id": user_id}).get(
                    "Item"
                )
                if existing_user:
                    if any(
                        existing_user.get(attr) != filtered_attributes.get(attr)
                        for attr in filtered_attributes
                    ):
                        filtered_attributes.pop("user_id", None)
                        # update dynamo expression can not handle ':' so we need to replace with a placeholder '_'
                        dynamo_table.update_item(
                            Key={"user_id": user_id},
                            UpdateExpression="SET "
                            + ", ".join(
                                f'#{k.replace(":", "_")}=:{k.replace(":", "_")}'
                                for k in filtered_attributes
                            ),
                            ExpressionAttributeNames={
                                f'#{k.replace(":", "_")}': k
                                for k in filtered_attributes
                            },
                            ExpressionAttributeValues={
                                f':{k.replace(":", "_")}': v
                                for k, v in filtered_attributes.items()
                            },
                        )
                        print(f"Updated user: {user_id}")
                else:
                    dynamo_table.put_item(Item=filtered_attributes)
                    print(f"Created user: {user_id}")
            else:
                print("No email found for the user, skipping...")

        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break

    # Now that all users are processed, update the admin table for AMPLIFY_GROUPS
    config_id = "amplifyGroups"
    admin_item = admin_table.get_item(Key={"config_id": config_id}).get("Item", {})

    # Extract existing data or start fresh
    data = admin_item.get("data", {})

    # Update only the groups that appear in custom:saml_groups
    for g, members_list in group_members.items():
        data[g] = {"groupName": g, "members": members_list, "createdBy": "Cognito_Sync"}

    # Put the updated item back into the admin table
    admin_table.put_item(
        Item={
            "config_id": config_id,
            "data": data,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    )

    return {"statusCode": 200, "body": "Sync completed successfully"}


def parse_group_string(group_str):
    """Parses a group string formatted like a list and returns a Python list."""
    if group_str and group_str.startswith("[") and group_str.endswith("]"):
        return [item.strip() for item in group_str[1:-1].split(",")]
    return []
