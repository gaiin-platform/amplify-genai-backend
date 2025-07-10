import time
import uuid

import boto3
import os
import json


def stop_job(current_user, job_id):
    set_job_result(current_user, job_id, {"status": "stopped"})


def is_job_stopped(current_user, job_id):
    job_status = check_job_status(current_user, job_id)
    return job_status == "stopped"


def init_job_status(current_user, initial_status):

    job_id = str(uuid.uuid4())
    print(
        f"Initializing job status for {current_user}/{job_id} with status {initial_status}"
    )
    table_name = os.getenv("JOB_STATUS_TABLE")
    if not table_name:
        raise ValueError("Environment variable JOB_STATUS_TABLE is not set")

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        # Create/overwrite item with initial status and timestamp
        table.put_item(
            Item={
                "user": current_user,
                "job_id": job_id,
                "status": initial_status,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
        )
        print(f"Job status initialized for {current_user}/{job_id}")
        return job_id
    except Exception as e:
        print(f"Error initializing job status: {e}")
        raise RuntimeError(f"Error initializing job status: {e}")


def update_job_status(current_user, job_id, status):

    print(f"Updating job status for {current_user}/{job_id} to {status}")
    # Environment variable for DynamoDB table
    table_name = os.getenv("JOB_STATUS_TABLE")
    if not table_name:
        raise ValueError("Environment variable JOB_STATUS_TABLE is not set")

    # DynamoDB client
    dynamodb = boto3.resource("dynamodb")

    # Access the DynamoDB table
    table = dynamodb.Table(table_name)

    try:
        # Update the status in the DynamoDB table
        table.update_item(
            Key={"user": current_user, "job_id": job_id},
            UpdateExpression="SET #s = :status, updated_at = :timestamp",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":timestamp": int(time.time()),
            },
        )
        print(f"Job status updated for {current_user}/{job_id} to {status}")
        return {"message": "Job status updated successfully"}
    except Exception as e:
        print(f"Error updating job status: {e}")
        raise RuntimeError(f"Error updating job status: {e}")


def set_job_result(current_user, job_id, result, store_in_s3=False):
    # Environment variable for DynamoDB table
    table_name = os.getenv("JOB_STATUS_TABLE")
    if not table_name:
        raise ValueError("Environment variable JOB_STATUS_TABLE is not set")

    # DynamoDB and S3 clients
    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")

    # Access the DynamoDB table
    table = dynamodb.Table(table_name)

    try:
        # Check if we need to store the result in S3
        if store_in_s3:
            print(f"Storing result in S3 for {current_user}/{job_id}")
            # Environment variable for S3 bucket
            bucket_name = os.getenv("JOB_RESULTS_BUCKET")
            if not bucket_name:
                raise ValueError("Environment variable JOB_RESULTS_BUCKET is not set")

            # Generate an S3 key
            s3_key = f"{current_user}/{job_id}/result.json"

            print(f"Uploading result to S3: {bucket_name}/{s3_key}")

            # Upload result to S3
            s3.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=json.dumps(result),
                ContentType="application/json",
            )

            print(f"Result uploaded to S3: {bucket_name}/{s3_key}")

            # Update DynamoDB with S3 result reference
            table.update_item(
                Key={"user": current_user, "job_id": job_id},
                UpdateExpression="SET stored_result = :s3_ref, #s = :status, updated_at = :timestamp",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s3_ref": {"bucket": bucket_name, "key": s3_key},
                    ":status": "finished",
                    ":timestamp": int(time.time()),
                },
            )

            print(f"Stored result reference in DynamoDB for {current_user}/{job_id}")

            return {"message": "Result stored in S3 and reference updated in DynamoDB"}

        print(f"Storing result directly in DynamoDB for {current_user}/{job_id}")

        # If result fits in DynamoDB, store it directly
        table.update_item(
            Key={"user": current_user, "job_id": job_id},
            UpdateExpression="SET job_result = :res, #s = :status, updated_at = :timestamp",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":res": result,
                ":status": "finished",
                ":timestamp": int(time.time()),
            },
        )

        print(f"Result stored directly in DynamoDB for {current_user}/{job_id}")

        return {"message": "Result stored directly in DynamoDB"}

    except Exception as e:
        print(f"Error setting job result: {e}")
        raise RuntimeError(f"Error setting job result: {e}")


# Example usage
# result_data = {"status": "complete", "output": "some data"}
# set_job_result("user123", "job123", result_data, store_in_s3=True)


def check_job_status(current_user, job_id):
    # Environment variable for DynamoDB table
    table_name = os.getenv("JOB_STATUS_TABLE")

    if not table_name:
        raise ValueError("Environment variable JOB_STATUS_TABLE is not set")

    # DynamoDB and S3 clients
    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")

    # Access the DynamoDB table
    table = dynamodb.Table(table_name)

    try:
        # Query the DynamoDB table
        response = table.get_item(Key={"user": current_user, "job_id": job_id})
    except Exception as e:
        raise RuntimeError(f"Error querying DynamoDB: {e}")

    # Check if the item exists
    item = response.get("Item")
    if not item:
        return "Job not found"

    # Handle the result key
    if "job_result" in item:
        return item["job_result"]

    # Handle the stored result key
    if "stored_result" in item:
        try:
            stored_result = item["stored_result"]
            bucket_name = stored_result.get("bucket")
            s3_key = stored_result.get("key")

            if not bucket_name or not s3_key:
                raise ValueError("Invalid stored result format in DynamoDB")

            # Retrieve data from S3
            s3_object = s3.get_object(Bucket=bucket_name, Key=s3_key)
            s3_data = s3_object["Body"].read().decode("utf-8")
            return json.loads(s3_data)
        except Exception as e:
            raise RuntimeError(f"Error retrieving data from S3: {e}")

    # Handle the status key
    if "status" in item:
        return item["status"]

    return "Unknown state"
