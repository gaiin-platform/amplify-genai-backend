import time
import uuid

import boto3
import os
import json

from pycommon.logger import getLogger
logger = getLogger("jobs")

def stop_job(current_user, job_id):
    set_job_result(current_user, job_id, {"status": "stopped"})


def is_job_stopped(current_user, job_id):
    job_status = check_job_status(current_user, job_id)
    return job_status == "stopped"


def init_job_status(current_user, initial_status):

    job_id = str(uuid.uuid4())
    logger.info("Initializing job status for %s/%s with status %s", current_user, job_id, initial_status)
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
        logger.info("Job status initialized for %s/%s", current_user, job_id)
        return job_id
    except Exception as e:
        logger.error("Error initializing job status: %s", e)
        raise RuntimeError(f"Error initializing job status: {e}")


def update_job_status(current_user, job_id, status):

    logger.info("Updating job status for %s/%s to %s", current_user, job_id, status)
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
        logger.info("Job status updated for %s/%s to %s", current_user, job_id, status)
        return {"message": "Job status updated successfully"}
    except Exception as e:
        logger.error("Error updating job status: %s", e)
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
            logger.info("Storing result in S3 for %s/%s", current_user, job_id)
            # Environment variable for S3 bucket
            bucket_name = os.getenv("JOB_RESULTS_BUCKET")
            if not bucket_name:
                raise ValueError("Environment variable JOB_RESULTS_BUCKET is not set")

            # Generate an S3 key
            s3_key = f"{current_user}/{job_id}/result.json"

            logger.info("Uploading result to S3: %s/%s", bucket_name, s3_key)

            # Upload result to S3
            s3.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=json.dumps(result),
                ContentType="application/json",
            )

            logger.info("Result uploaded to S3: %s/%s", bucket_name, s3_key)

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

            logger.info("Stored result reference in DynamoDB for %s/%s", current_user, job_id)

            return {"message": "Result stored in S3 and reference updated in DynamoDB"}

        logger.info("Storing result directly in DynamoDB for %s/%s", current_user, job_id)

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

        logger.info("Result stored directly in DynamoDB for %s/%s", current_user, job_id)

        return {"message": "Result stored directly in DynamoDB"}

    except Exception as e:
        logger.error("Error setting job result: %s", e)
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
