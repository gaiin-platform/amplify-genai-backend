# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime
import os
import re
import boto3
import json
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pycommon.const import APIAccessType

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value])

from pycommon.encoders import CustomPydanticJSONEncoder, LossyDecimalEncoder

from service.core import get_most_recent_assistant_version

# used for system users who have access to a group. Group assistants are based on group permissions
# currently the data returned is best for our amplify wordpress plugin
@validated(op="get")
def retrieve_astg_for_system_use(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters", {})
    print("Query params: ", query_params)
    assistantId = query_params.get("assistantId", "")
    pattern = r"^[a-zA-Z0-9-]+-\d{6}$"
    # must be in system user format
    if (
        not assistantId
        or assistantId[:6] == "astgp"
        or not re.match(pattern, current_user)
    ):
        return json.dumps(
            {
                "statusCode": 400,
                "body": {
                    "error": "Invalid or missing assistantId parameter or not a system user."
                },
            }
        )
    print("retrieving astgp data")
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    astgp = get_most_recent_assistant_version(assistants_table, assistantId)
    if not astgp:
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "AssistantId parameter does not match any assistant"},
            }
        )

    ast_data = astgp.get("data", {})

    groupId = ast_data.get("groupId", None)
    if not groupId:
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "The assistant does not have a groupId."},
            }
        )

    print("checking perms from group table")
    # check system user has access to group assistant
    groups_table = dynamodb.Table(os.environ["GROUPS_DYNAMO_TABLE"])

    try:
        response = groups_table.get_item(Key={"group_id": groupId})
        # Check if the item was found
        if "Item" in response:
            item = response["Item"]
            if current_user not in item.get("systemUsers", []):
                return json.dumps(
                    {
                        "statusCode": 401,
                        "body": {
                            "error": "User is not authorized to access assistant details"
                        },
                    }
                )
        else:
            return json.dumps(
                {
                    "statusCode": 400,
                    "body": {"error": "Item with group_id not found in dynamo"},
                }
            )

    except Exception as e:
        print(f"Error getting group from dynamo: {e}")
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "Failed to retrieve group from dynamo"},
            }
        )

    group_types_data = {
        group_type: {
            "isDisabled": details["isDisabled"],
            "disabledMessage": details["disabledMessage"],
        }
        for group_type, details in ast_data.get("groupTypeData", {}).items()
    }

    return {
        "statusCode": 200,
        "body": {
            "assistant": {
                "name": astgp["name"],
                "groupId": groupId,
                "instructions": astgp["instructions"],
                "group_types": group_types_data,
                "group_type_questions": ast_data.get("groupUserTypeQuestion", None),
                "model": ast_data.get("model", None),
                "disclaimer": astgp.get("disclaimer", None),
            }
        },
    }



# queries GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE (updated at the end of every conversation via amplify-lambda-js/common/chat/controllers/sequentialChat.js)
# to see all conversations of a specific group assistant. assistantId must be provided in the data field.
@validated(op="get_group_assistant_conversations")
def get_group_assistant_conversations(event, context, current_user, name, data):
    if "data" not in data or "assistantId" not in data["data"]:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "assistantId is required"}),
        }

    assistant_id = data["data"]["assistantId"]

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])

    try:
        response = table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_id),
        )

        conversations = response["Items"]
        # print(f"Found {len(conversations)} conversations for assistant {assistant_id}")
        # print(f"Conversations: {json.dumps(conversations, cls=CustomPydanticJSONEncoder)}")

        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            conversations.extend(response["Items"])

        return {
            "statusCode": 200,
            "body": json.dumps(conversations, cls=CustomPydanticJSONEncoder),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }



@validated(op="get_group_conversations_data")
def get_group_conversations_data(event, context, current_user, name, data):
    if (
        "data" not in data
        or "conversationId" not in data["data"]
        or "assistantId" not in data["data"]
    ):
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"error": "conversationId and assistantId are required"}
            ),
        }

    conversation_id = data["data"]["conversationId"]
    assistant_id = data["data"]["assistantId"]

    s3 = boto3.client("s3")
    bucket_name = os.environ["S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME"]
    key = f"{assistant_id}/{conversation_id}.txt"

    try:
        response = s3.get_object(Bucket=bucket_name, Key=key)
        content = response["Body"].read().decode("utf-8")

        return {
            "statusCode": 200,
            "body": json.dumps({"content": content}),
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Conversation not found"}),
            }
        else:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Error retrieving conversation content"}),
            }



# accessible via API gateway for users to collect data on a group assistant
# user MUST provide assistantId
# optional parameters to specify:
# - specify date range: startDate-endDate (default null, meaning provide all data regardless of date)
# - include conversation data: true/false (default false, meaning provide only dashboard data, NOT conversation statistics in CSV format)
# - include conversation content: true/false (default false, meaning content of conversations is not provided)
@validated(op="get_group_assistant_dashboards")
def get_group_assistant_dashboards(event, context, current_user, name, data):
    if "data" not in data or "assistantId" not in data["data"]:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "assistantId is required"}),
        }

    assistant_id = data["data"]["assistantId"]
    start_date = data["data"].get("startDate")
    end_date = data["data"].get("endDate")
    include_conversation_data = data["data"].get("includeConversationData", False)
    include_conversation_content = data["data"].get("includeConversationContent", False)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])
    # table = dynamodb.Table("group-assistant-conversations-content-test")

    try:
        response = table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_id),
        )

        conversations = response["Items"]

        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            conversations.extend(response["Items"])

        # Filter conversations by date range if specified
        if start_date and end_date:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            conversations = [
                conv
                for conv in conversations
                if start <= datetime.fromisoformat(conv.get("timestamp", "")) <= end
            ]

        # Prepare dashboard data
        assistant_name = (
            conversations[0].get("assistantName", "") if conversations else ""
        )
        unique_users = set(conv.get("user", "") for conv in conversations)
        total_prompts = sum(int(conv.get("numberPrompts", 0)) for conv in conversations)
        total_conversations = len(conversations)

        entry_points = {}
        categories = {}
        employee_types = {}
        user_employee_types = {}
        total_user_rating = 0
        total_system_rating = 0
        user_rating_count = 0
        system_rating_count = 0

        for conv in conversations:
            # Determine entry points
            entry_points[conv.get("entryPoint", "")] = (
                entry_points.get(conv.get("entryPoint", ""), 0) + 1
            )

            # Determine categories
            category = (conv.get("category") or "").strip()
            if category:  # Only add non-empty categories
                categories[category] = categories.get(category, 0) + 1

            # Update user_employee_types
            user = conv.get("user", "")
            employee_type = conv.get("employeeType", "")
            if user not in user_employee_types:
                user_employee_types[user] = employee_type
                employee_types[employee_type] = employee_types.get(employee_type, 0) + 1

            # Calculate user rating
            user_rating = conv.get("userRating")
            if user_rating is not None:
                try:
                    total_user_rating += float(user_rating)
                    user_rating_count += 1
                except ValueError:
                    print(f"Invalid user rating value: {user_rating}")

            # Calculate system rating
            system_rating = conv.get("systemRating")
            if system_rating is not None:
                try:
                    total_system_rating += float(system_rating)
                    system_rating_count += 1
                except ValueError:
                    print(f"Invalid system rating value: {system_rating}")

        average_user_rating = (
            float(total_user_rating) / float(user_rating_count)
            if user_rating_count > 0
            else None
        )
        average_system_rating = (
            float(total_system_rating) / float(system_rating_count)
            if system_rating_count > 0
            else None
        )

        dashboard_data = {
            "assistantId": assistant_id,
            "assistantName": assistant_name,
            "numUsers": len(unique_users),
            "totalConversations": total_conversations,
            "averagePromptsPerConversation": (
                float(total_prompts) / float(total_conversations)
                if total_conversations > 0
                else 0.0
            ),
            "entryPointDistribution": entry_points,
            "categoryDistribution": categories,
            "employeeTypeDistribution": employee_types,
            "averageUserRating": average_user_rating,
            "averageSystemRating": average_system_rating,
        }

        response_data = {"dashboardData": dashboard_data}

        if include_conversation_data or include_conversation_content:
            s3 = boto3.client("s3")
            bucket_name = os.environ["S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME"]

            for conv in conversations:
                if include_conversation_content:
                    conversation_id = conv.get("conversationId")
                    if conversation_id:
                        key = f"{assistant_id}/{conversation_id}.txt"
                        try:
                            obj = s3.get_object(Bucket=bucket_name, Key=key)
                            conv["conversationContent"] = (
                                obj["Body"].read().decode("utf-8")
                            )
                        except ClientError as e:
                            if e.response["Error"]["Code"] == "NoSuchKey":
                                print(
                                    f"Conversation content not found for {conversation_id}"
                                )
                            else:
                                print(
                                    f"Error retrieving S3 content for conversation {conversation_id}: {str(e)}"
                                )

            # response_data["conversationData"] = conversations

            # Generate a unique filename
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"conversation_data_{assistant_id}_{timestamp}.json"

            # Upload conversation data to S3
            s3.put_object(
                Bucket=bucket_name,
                Key=filename,
                Body=json.dumps(conversations, cls=CustomPydanticJSONEncoder),
                ContentType="application/json",
            )

            # Generate a pre-signed URL that's valid for 1 hour
            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": filename},
                ExpiresIn=3600,
            )

            response_data["conversationDataUrl"] = presigned_url

        return {
            "statusCode": 200,
            "body": json.dumps(response_data, cls=CustomPydanticJSONEncoder),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }



@validated(op="save_user_rating")
def save_user_rating(event, context, current_user, name, data):
    if (
        "data" not in data
        or "conversationId" not in data["data"]
        or "userRating" not in data["data"]
    ):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "conversationId and userRating are required"}),
        }

    conversation_id = data["data"]["conversationId"]
    user_rating = data["data"]["userRating"]
    user_feedback = data["data"].get("userFeedback")  # Get userFeedback if present

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])

    try:
        # Construct the UpdateExpression based on whether userFeedback is present
        update_expression = "SET userRating = :rating"
        expression_attribute_values = {":rating": user_rating}

        if user_feedback:
            update_expression += ", userFeedback = :feedback"
            expression_attribute_values[":feedback"] = user_feedback

        response = table.update_item(
            Key={"conversationId": conversation_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="UPDATED_NEW",
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": (
                        "User rating and feedback saved successfully"
                        if user_feedback
                        else "User rating saved successfully"
                    ),
                    "updatedAttributes": response.get("Attributes"),
                },
                cls=LossyDecimalEncoder,
            ),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }
