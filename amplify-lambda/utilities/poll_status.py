"""
Poll Status Endpoint

Provides a simple GET endpoint to check the status of long-running operations.
Frontend polls this endpoint using the pollRequestId to track progress.
"""

import os
import json
import time
import boto3
from typing import Dict, Any
from pycommon.logger import getLogger
from pycommon.authz import validated
from pycommon.encoders import CustomPydanticJSONEncoder

logger = getLogger("poll-status")


@validated("get_poll_status", validate_body=False)
def get_poll_status_handler(event: Dict[str, Any], context: Any, current_user: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get the status of a polling request.

    Query Parameters:
        requestId: The poll request ID to check

    Returns:
        Poll status record with current progress, logs, and status
    """
    try:
        # Get requestId from query parameters
        query_params = event.get("queryStringParameters") or {}
        request_id = query_params.get("requestId")

        if not request_id:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "success": False,
                    "error": "requestId query parameter is required"
                })
            }

        # Get the poll status table name
        table_name = os.environ.get("POLL_STATUS_TABLE")
        if not table_name:
            logger.error("POLL_STATUS_TABLE environment variable not set")
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "success": False,
                    "error": "Poll status tracking not configured"
                })
            }

        # Long polling: Check DynamoDB every 2 seconds for up to 25 seconds
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)

        max_wait_seconds = 25
        check_interval_seconds = 3
        start_time = time.time()

        item = None
        while True:
            response = table.get_item(
                Key={
                    "requestId": request_id,
                    "user": current_user
                }
            )

            # Check if record exists
            if "Item" not in response:
                return {
                    "statusCode": 404,
                    "body": json.dumps({
                        "success": False,
                        "error": "Poll request not found or already completed",
                        "requestId": request_id
                    })
                }

            item = response["Item"]
            status = item.get("status")

            # If status is completed or failed, return immediately
            if status in ["completed", "failed"]:
                result_preview = str(item.get("result", ""))[:200] if item.get("result") else "None"
                error_preview = str(item.get("error", ""))[:200] if item.get("error") else "None"
                logger.info(
                    f"Poll request {request_id} reached terminal state: {status}\n"
                    f"  Result preview: {result_preview}\n"
                    f"  Error preview: {error_preview}\n"
                    f"  Has result: {item.get('result') is not None}\n"
                    f"  Has error: {item.get('error') is not None}"
                )

                # DELETE THE ROW before returning (user wants immediate cleanup)
                logger.info(f"Deleting poll status record for {request_id}")
                table.delete_item(
                    Key={
                        "requestId": request_id,
                        "user": current_user
                    }
                )
                logger.info(f"Poll status record deleted for {request_id}")
                break

            # Check if we've exceeded max wait time
            elapsed = time.time() - start_time
            if elapsed >= max_wait_seconds:
                logger.info(f"Poll request {request_id} still processing after {elapsed:.1f}s, returning current state")
                break

            # Wait before next check
            logger.debug(f"Poll request {request_id} still {status}, waiting {check_interval_seconds}s before next check")
            time.sleep(check_interval_seconds)

        # Return the poll status
        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "data": {
                    "requestId": item.get("requestId"),
                    "status": item.get("status"),
                    "lastLog": item.get("lastLog"),
                    "lastLogLevel": item.get("lastLogLevel"),
                    "createdAt": item.get("createdAt"),
                    "updatedAt": item.get("updatedAt"),
                    "completedAt": item.get("completedAt"),
                    "result": item.get("result"),
                    "error": item.get("error")
                }
            }, cls=CustomPydanticJSONEncoder)
        }

    except Exception as e:
        logger.error(f"Error getting poll status: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }
