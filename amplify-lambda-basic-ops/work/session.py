import boto3
import uuid
import time
import os
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

# Get table and bucket names from environment variables
SESSIONS_TABLE_NAME = os.environ.get("USER_SESSIONS_DYNAMODB_TABLE_NAME")
RECORDS_TABLE_NAME = os.environ.get("USER_RECORDS_DYNAMODB_TABLE_NAME")
S3_BUCKET_NAME = os.environ.get("ATTACHMENT_STORAGE_S3_BUCKET_NAME")

if not all([SESSIONS_TABLE_NAME, RECORDS_TABLE_NAME, S3_BUCKET_NAME]):
    raise ValueError("Required environment variables are not set")

sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)
records_table = dynamodb.Table(RECORDS_TABLE_NAME)


from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


def create_session(username, conversation_id=None, tags=None, metadata=None):
    # Get the current max session_id for this user
    response = sessions_table.query(
        KeyConditionExpression=Key("username").eq(username),
        ProjectionExpression="session_id",
        ScanIndexForward=False,
        Limit=1,
    )

    # Determine the next session_id
    if response["Items"]:
        session_id = response["Items"][0]["session_id"] + 1
    else:
        session_id = 1

    current_time = time.strftime("%Y-%m-%dT%H:%M:%S")

    session_item = {
        "username": username,
        "session_id": session_id,
        "created_at": current_time,
        "last_accessed": current_time,
        "status": "active",
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "tags": tags or [],
        "metadata": metadata or {},
        "records_count": 0,
        "session_duration": 0,
        "last_activity_type": "session_created",
    }

    # Use conditional write to ensure uniqueness
    try:
        sessions_table.put_item(
            Item=session_item, ConditionExpression="attribute_not_exists(session_id)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # If this happens, retry the operation
            return create_session(username, conversation_id, tags, metadata)
        else:
            raise

    return session_item


def update_session(username, session_id, update_data):
    update_expression = "SET "
    expression_attribute_values = {":last_accessed": int(time.time())}
    expression_attribute_names = {"#last_accessed": "last_accessed"}

    for key, value in update_data.items():
        update_expression += f"#{key} = :{key}, "
        expression_attribute_values[f":{key}"] = value
        expression_attribute_names[f"#{key}"] = key

    update_expression += "#last_accessed = :last_accessed"

    response = sessions_table.update_item(
        Key={"username": username, "session_id": session_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues=expression_attribute_values,
        ExpressionAttributeNames=expression_attribute_names,
        ReturnValues="ALL_NEW",
    )

    return response["Attributes"]


def add_record(username, session_id, record_data, attachments=None):
    # Check if session is active
    key = {"username": username, "session_id": int(session_id)}
    print(f"Searching for session with key={key}")

    response = sessions_table.get_item(Key=key)

    if "Item" not in response:
        raise ValueError(f"Session not found for key: {key}")

    session = response["Item"]
    print(f"Found session: {session}")

    if session["status"] != "active":
        raise ValueError(f"Session is not active. Status: {session['status']}")

    # Get the current max record_id for this session
    response = records_table.query(
        KeyConditionExpression=Key("session_id").eq(int(session_id)),
        ProjectionExpression="record_id",
        ScanIndexForward=False,
        Limit=1,
    )

    # Determine the next record_id
    if response["Items"]:
        record_id = response["Items"][0]["record_id"] + 1
    else:
        record_id = 1

    current_time = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Process attachments
    attachment_pointers = {}
    if attachments:
        for att_name, att_content in attachments.items():
            s3_key = f"{session_id}/{record_id}/{uuid.uuid4()}_{att_name}"
            s3.put_object(Bucket=S3_BUCKET_NAME, Key=s3_key, Body=att_content)
            attachment_pointers[att_name] = s3_key

    # Create record
    record_item = {
        "session_id": int(session_id),
        "record_id": int(record_id),
        "username": username,
        "created_at": current_time,
        "data": record_data,
        "attachment_pointers": attachment_pointers,
    }

    # Use conditional write to ensure uniqueness
    try:
        records_table.put_item(
            Item=record_item, ConditionExpression="attribute_not_exists(record_id)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # If this happens, retry the operation
            return add_record(username, session_id, record_data, attachments)
        else:
            raise

    # Calculate session duration
    session_created_at = datetime.strptime(session["created_at"], "%Y-%m-%dT%H:%M:%S")
    current_datetime = datetime.strptime(current_time, "%Y-%m-%dT%H:%M:%S")
    session_duration = int((current_datetime - session_created_at).total_seconds())

    # Update session
    update_session(
        username,
        session_id,
        {
            "records_count": session["records_count"] + 1,
            "last_activity_type": "record_added",
            "session_duration": session_duration,
        },
    )

    return record_item


def delete_record(username, session_id, record_id):
    try:
        # Check if session is active
        session = sessions_table.get_item(
            Key={"username": username, "session_id": int(session_id)}
        )["Item"]
        if not session or session["status"] != "active":
            raise ValueError("Session is not active")

        # Get the record before deleting it
        record = records_table.get_item(
            Key={"session_id": int(session_id), "record_id": int(record_id)}
        )
        if "Item" not in record:
            raise ValueError("Record not found")
        record = record["Item"]

        # Delete record
        records_table.delete_item(
            Key={"session_id": int(session_id), "record_id": int(record_id)}
        )

        # Delete attachments
        for s3_key in record.get("attachment_pointers", {}).values():
            try:
                s3.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
            except ClientError as e:
                print(f"Error deleting attachment {s3_key}: {e}")
                # Continue with deletion even if an attachment fails to delete

        current_time = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Calculate session duration
        session_created_at = datetime.strptime(
            session["created_at"], "%Y-%m-%dT%H:%M:%S"
        )
        current_datetime = datetime.strptime(current_time, "%Y-%m-%dT%H:%M:%S")
        session_duration = int((current_datetime - session_created_at).total_seconds())

        # Update session
        update_session(
            username,
            session_id,
            {
                "records_count": max(
                    0, session["records_count"] - 1
                ),  # Ensure count doesn't go negative
                "last_activity_type": "record_deleted",
                "session_duration": session_duration,
            },
        )

        return {
            "deleted_record_id": record_id,
            "remaining_records_count": max(0, session["records_count"] - 1),
        }

    except ClientError as e:
        print(f"Error in delete_record: {e}")
        raise ValueError("Failed to delete record")


def list_records(username, session_id):
    try:
        # Check if session is active
        session = sessions_table.get_item(
            Key={"username": username, "session_id": int(session_id)}
        )
        if "Item" not in session or session["Item"]["status"] != "active":
            raise ValueError("Session is not active or does not exist")

        # Query records for the session
        response = records_table.query(
            KeyConditionExpression=Key("session_id").eq(int(session_id))
        )

        # Sort the records based on their creation time
        sorted_records = sorted(response["Items"], key=lambda x: x["created_at"])

        return sorted_records

    except ClientError as e:
        print(f"Error in list_records: {e}")
        raise ValueError("Failed to list records")


def fetch_record(username, session_id, record_id):
    try:
        # Check if session is active
        session_response = sessions_table.get_item(
            Key={"username": username, "session_id": int(session_id)}
        )
        if (
            "Item" not in session_response
            or session_response["Item"]["status"] != "active"
        ):
            raise ValueError("Session is not active or does not exist")

        # Fetch the record
        record_response = records_table.get_item(
            Key={"session_id": int(session_id), "record_id": int(record_id)}
        )
        if "Item" not in record_response:
            raise ValueError(
                f"Record with id {record_id} not found in session {session_id}"
            )

        return record_response["Item"]

    except ClientError as e:
        print(f"Error in fetch_record: {e}")
        raise ValueError("Failed to fetch record")


def fetch_attachment(username, session_id, record_id, attachment_name):
    try:
        # Check if session is active
        session_response = sessions_table.get_item(
            Key={"username": username, "session_id": int(session_id)}
        )
        if (
            "Item" not in session_response
            or session_response["Item"]["status"] != "active"
        ):
            raise ValueError("Session is not active or does not exist")

        # Fetch the record
        record_response = records_table.get_item(
            Key={"session_id": int(session_id), "record_id": int(record_id)}
        )
        if "Item" not in record_response:
            raise ValueError(
                f"Record with id {record_id} not found in session {session_id}"
            )

        record = record_response["Item"]

        # Check if the attachment exists
        s3_key = record.get("attachment_pointers", {}).get(attachment_name)
        if not s3_key:
            raise ValueError(
                f"Attachment {attachment_name} not found for record {record_id}"
            )

        # Fetch the attachment from S3
        try:
            response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
            return response["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise ValueError(f"Attachment {attachment_name} not found in S3")
            else:
                raise

    except ClientError as e:
        print(f"Error in fetch_attachment: {e}")
        raise ValueError("Failed to fetch attachment")
