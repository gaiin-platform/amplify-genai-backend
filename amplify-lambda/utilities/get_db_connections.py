import json
import boto3
import os
import logging
from typing import Dict, Any
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

# Configure logging for serverless offline with more detailed format
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG level for more verbose logging
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)  # Changed to use module name

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DB_CONNECTIONS_TABLE"])


@validated("get_connections")
def lambda_handler(
    event: Dict[str, Any],
    context: Any,
    current_user: str,
    name: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    # logger.debug("=" * 100)
    # logger.debug("🚀 Starting get_db_connections lambda handler")
    # logger.debug("Event: %s", json.dumps(event, indent=2))
    # logger.debug("Context: %s", str(context))
    # logger.debug("Current User: %s", current_user)
    # logger.debug("Name: %s", name)
    # logger.debug("Data: %s", json.dumps(data, indent=2))
    # logger.debug("=" * 100)

    try:
        # Use current_user instead of getting user from data
        user = current_user
        # logger.debug("📝 Using current_user: %s", user)

        if not user:
            logger.error("❌ Current user is missing")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Current user is required"}),
            }

        # logger.info("🔍 Querying database for user: %s", user)
        # logger.debug("Using table: %s", os.environ["DB_CONNECTIONS_TABLE"])

        # Query the table using the UserIndex GSI
        try:
            response = table.query(
                IndexName="UserIndex",
                KeyConditionExpression="#user = :user",
                ExpressionAttributeNames={"#user": "user"},
                ExpressionAttributeValues={":user": user},
            )
            # logger.debug("Raw DynamoDB response: %s", json.dumps(response, indent=2))
        except Exception as db_error:
            logger.error("❌ Database query failed: %s", str(db_error))
            raise

        # Get the items and prepare detailed response
        items = response.get("Items", [])
        # logger.debug("Number of items retrieved: %d", len(items))

        # Mask sensitive fields in the response for security
        def mask_sensitive_fields(item):
            """Mask sensitive fields in database connection items."""
            masked_item = item.copy()
            sensitive_fields = ["password", "credentials"]

            for field in sensitive_fields:
                if field in masked_item and masked_item[field]:
                    # Replace with asterisks to indicate the field exists but hide the value
                    masked_item[field] = "********"

            return masked_item

        # Apply masking to all items
        masked_items = [mask_sensitive_fields(item) for item in items]

        # Create a detailed response that includes both the connections and debug info
        response_body = {
            "connections": masked_items,
            "debug_info": {
                "user": user,
                "total_connections": len(items),
                "raw_response": response,
            },
        }

        # Enhanced logging for better visibility
        # logger.info("\n" + "=" * 100)
        # logger.info("📊 DATABASE QUERY RESULTS")
        # logger.info("=" * 100)
        # logger.info("👤 User: %s", user)
        # logger.info("🔢 Total Connections Found: %d", len(items))
        # logger.info("=" * 100)

        if items:
            for idx, item in enumerate(items, 1):
                # logger.info("\n📌 Connection #%d:", idx)
                # logger.info("-" * 50)
                # Pretty print each connection with better formatting
                formatted_item = json.dumps(item, indent=2)
                # logger.info(formatted_item)
                # logger.info("-" * 50)
        else:
            logger.info("\n❌ No connections found for this user.")

        # logger.info("\n" + "=" * 100)
        # logger.info("✅ Query completed successfully")
        # logger.info("=" * 100 + "\n")

        # Return the items found with debug info
        return {
            "statusCode": 200,
            "body": json.dumps(response_body, indent=2),
        }

    except Exception as e:
        error_response = {
            "error": str(e),
            "debug_info": {
                "user": user if "user" in locals() else None,
                "error_type": type(e).__name__,
            },
        }
        # logger.error("\n" + "!" * 100)
        # logger.error("❌ ERROR IN GET_DB_CONNECTIONS")
        # logger.error("!" * 100)
        # logger.error("Error Type: %s", type(e).__name__)
        # logger.error("Error Message: %s", str(e))
        # logger.error("Stack Trace:", exc_info=True)  # Added stack trace
        # logger.error("!" * 100 + "\n")
        return {"statusCode": 500, "body": json.dumps(error_response, indent=2)}
