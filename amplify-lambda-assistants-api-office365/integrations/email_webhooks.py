"""
Microsoft Graph Email Webhook System
====================================

This module handles Microsoft Graph webhook notifications for incoming emails.
It provides three main functions:
1. webhook_handler - Receives webhook notifications from Microsoft Graph
2. email_processor - Processes emails from SQS queue
3. create_subscription - Creates webhook subscriptions for users

Phase 1: Single user testing with manual subscription management
Future Phase 2: Multi-user automation and subscription renewal
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import requests

from pycommon.api.secrets import get_secret_parameter
from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, SSMOperation, SQSOperation
)
from pycommon.logger import getLogger
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

logger = getLogger("email_webhooks")
setup_validated(rules, get_permission_checker)

# Constants
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
PROVIDER = "microsoft"
SUBSCRIPTION_EXPIRATION_HOURS = 72  # 3 days maximum for Graph webhooks


class EmailWebhookError(Exception):
    """Base exception for email webhook operations"""
    pass


class GraphAPIError(EmailWebhookError):
    """Raised when Graph API operations fail"""
    pass


class ValidationError(EmailWebhookError):
    """Raised when webhook validation fails"""
    pass


def get_graph_application_token() -> str:
    """
    Get application-level Graph API token for organization-wide access.
    Uses existing credential pattern from oauth.py but for application permissions.
    
    Returns:
        Access token for Graph API application permissions
    """
    try:
        stage = os.environ.get("INTEGRATION_STAGE", "dev")
        secret_param = f"integrations/{PROVIDER}/{stage}"
        
        logger.info(f"Retrieving Graph credentials from /oauth/{secret_param}")
        secrets_value = get_secret_parameter(secret_param, "/oauth")
        
        if not secrets_value:
            raise GraphAPIError("No Microsoft Graph credentials found in parameter store")
            
        secrets_json = json.loads(secrets_value)
        secrets_data = secrets_json["client_config"]["web"]
        
        client_id = secrets_data["client_id"]
        client_secret = secrets_data["client_secret"] 
        tenant_id = secrets_data["tenant_id"]
        
        # Get application token (not delegated user token)
        auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        
        auth_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"
        }
        
        logger.info("Requesting application token from Microsoft")
        response = requests.post(auth_url, data=auth_data)
        response.raise_for_status()
        
        token_data = response.json()
        return token_data["access_token"]
        
    except Exception as e:
        logger.error(f"Failed to get Graph application token: {str(e)}")
        raise GraphAPIError(f"Authentication failed: {str(e)}")


def get_graph_headers() -> Dict[str, str]:
    """Get headers for Graph API requests with application token"""
    token = get_graph_application_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


@required_env_vars({
    "EMAIL_WEBHOOK_CLIENT_STATE": [SSMOperation.GET_PARAMETER],
})
def webhook_handler(event, context):
    """
    Handle Microsoft Graph webhook notifications for incoming emails.
    
    This function handles two types of requests:
    1. Validation requests (GET with validationToken parameter)
    2. Notification requests (POST with notification data)
    
    For validation: Returns the validationToken as plain text
    For notifications: Validates clientState and sends to SQS queue
    
    Must respond within 3 seconds or Microsoft will retry.
    """
    try:
        logger.info(f"Webhook handler called with method: {event.get('httpMethod')}")
        
        # Handle validation request (Microsoft sends GET with ?validationToken=xyz)
        if event.get("httpMethod") == "GET":
            query_params = event.get("queryStringParameters") or {}
            validation_token = query_params.get("validationToken")
            
            if validation_token:
                logger.info("Handling webhook validation request")
                return {
                    "statusCode": 200,
                    "body": validation_token,  # Return plain text, not JSON
                    "headers": {"Content-Type": "text/plain"}
                }
            else:
                logger.warning("GET request without validationToken")
                return {
                    "statusCode": 400,
                    "body": "Missing validationToken parameter"
                }
        
        # Handle notification request (POST with notification data)
        elif event.get("httpMethod") == "POST":
            return _handle_notification(event, context)
        
        else:
            logger.warning(f"Unsupported HTTP method: {event.get('httpMethod')}")
            return {
                "statusCode": 405,
                "body": json.dumps({"error": "Method not allowed"})
            }
            
    except Exception as e:
        logger.error(f"Webhook handler error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"})
        }


def _handle_notification(event, context) -> Dict[str, Any]:
    """Handle POST notification from Microsoft Graph"""
    try:
        # Parse notification body
        body = event.get("body", "{}")
        if isinstance(body, str):
            notification_data = json.loads(body)
        else:
            notification_data = body
            
        logger.info(f"Received notification: {json.dumps(notification_data)}")
        
        # Extract notifications array
        notifications = notification_data.get("value", [])
        if not notifications:
            logger.warning("No notifications in request body")
            return {"statusCode": 200, "body": "OK"}
        
        # Validate and process each notification
        processed_count = 0
        for notification in notifications:
            if _process_single_notification(notification):
                processed_count += 1
        
        logger.info(f"Successfully processed {processed_count}/{len(notifications)} notifications")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "processed": processed_count,
                "total": len(notifications)
            })
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in notification body: {str(e)}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON"})
        }
    except Exception as e:
        logger.error(f"Notification processing error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Processing failed"})
        }


def _process_single_notification(notification: Dict[str, Any]) -> bool:
    """Process a single notification and send to SQS"""
    try:
        # Validate required fields
        subscription_id = notification.get("subscriptionId")
        client_state = notification.get("clientState")
        change_type = notification.get("changeType")
        resource = notification.get("resource")
        
        if not all([subscription_id, client_state, change_type, resource]):
            logger.error(f"Missing required fields in notification: {notification}")
            return False
        
        # Validate clientState for security
        expected_client_state = os.environ.get("EMAIL_WEBHOOK_CLIENT_STATE")
        if client_state != expected_client_state:
            logger.error(f"Invalid clientState: {client_state}")
            raise ValidationError("Invalid clientState")
        
        # Extract message ID and user info from resource
        # Resource format: users/{userId}/mailFolders('Inbox')/messages/{messageId}
        if "/messages/" not in resource:
            logger.warning(f"Unexpected resource format: {resource}")
            return False
            
        # Parse user ID and message ID from resource
        # Example: users/user@example.com/mailFolders('Inbox')/messages/AAMkAG...
        resource_parts = resource.split("/")
        user_id = resource_parts[1] if len(resource_parts) > 1 else None
        message_id = resource_parts[-1] if len(resource_parts) > 0 else None
        
        if not user_id or not message_id:
            logger.error(f"Could not parse user_id or message_id from resource: {resource}")
            return False
        
        # Prepare message for SQS
        sqs_message = {
            "subscriptionId": subscription_id,
            "changeType": change_type,
            "resource": resource,
            "userId": user_id,
            "messageId": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resourceData": notification.get("resourceData", {})
        }
        
        # Send to SQS queue
        queue_url = _get_sqs_queue_url()
        sqs = boto3.client("sqs")
        
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(sqs_message),
            MessageAttributes={
                "subscriptionId": {
                    "StringValue": subscription_id,
                    "DataType": "String"
                },
                "changeType": {
                    "StringValue": change_type,
                    "DataType": "String"
                },
                "userId": {
                    "StringValue": user_id,
                    "DataType": "String"
                }
            }
        )
        
        logger.info(f"Sent notification to SQS: MessageId={response['MessageId']}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to process notification: {str(e)}")
        return False


def _get_sqs_queue_url() -> str:
    """Get SQS queue URL from environment or construct it"""
    queue_url = os.environ.get("EMAIL_NOTIFICATIONS_QUEUE_URL")
    if queue_url:
        return queue_url
    
    # Fallback: construct URL from queue name
    queue_name = os.environ.get("EMAIL_NOTIFICATIONS_QUEUE")
    if not queue_name:
        raise EmailWebhookError("No SQS queue configuration found")
    
    region = os.environ.get("AWS_REGION", "us-east-1")
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    return f"https://sqs.{region}.amazonaws.com/{account_id}/{queue_name}"


@required_env_vars({
    "EMAIL_SUBSCRIPTIONS_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.GET_ITEM],
})
def email_processor(event, context):
    """
    Process email notifications from SQS queue.
    
    This function:
    1. Receives email notification messages from SQS
    2. Fetches full email content from Graph API
    3. Logs email details for Phase 1
    4. Placeholder for future AI assistant integration
    
    Handles multiple messages in a batch for efficiency.
    """
    try:
        logger.info(f"Email processor called with {len(event.get('Records', []))} messages")
        
        processed_count = 0
        failed_count = 0
        
        for record in event.get("Records", []):
            try:
                # Parse SQS message
                message_body = json.loads(record["body"])
                logger.info(f"Processing email notification: {message_body}")
                
                # Extract notification details
                user_id = message_body["userId"]
                message_id = message_body["messageId"]
                subscription_id = message_body["subscriptionId"]
                change_type = message_body["changeType"]
                
                # Skip non-creation events for Phase 1
                if change_type != "created":
                    logger.info(f"Skipping {change_type} event for message {message_id}")
                    processed_count += 1
                    continue
                
                # Fetch full email from Graph API
                email_data = _fetch_email_from_graph(user_id, message_id)
                
                if email_data:
                    # Log email details for Phase 1
                    _log_email_details(email_data, user_id, subscription_id)
                    
                    # Store notification in DynamoDB for tracking
                    _store_email_notification(message_body, email_data)
                    
                    # TODO Phase 2: Call AI assistant API
                    # assistant_response = _call_ai_assistant(user_id, email_data)
                    
                    processed_count += 1
                    logger.info(f"Successfully processed email {message_id} for user {user_id}")
                else:
                    logger.warning(f"Could not fetch email {message_id} for user {user_id}")
                    failed_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to process individual message: {str(e)}")
                failed_count += 1
        
        logger.info(f"Email processing complete: {processed_count} processed, {failed_count} failed")
        
        # Return success - SQS will remove successfully processed messages
        return {
            "batchItemFailures": []  # Empty means all messages processed successfully
        }
        
    except Exception as e:
        logger.error(f"Email processor error: {str(e)}")
        # Return all messages as failed for retry
        return {
            "batchItemFailures": [
                {"itemIdentifier": record["messageId"]} 
                for record in event.get("Records", [])
            ]
        }


def _fetch_email_from_graph(user_id: str, message_id: str) -> Optional[Dict[str, Any]]:
    """Fetch full email content from Microsoft Graph API"""
    try:
        headers = get_graph_headers()
        
        # Use the users/{userId}/messages/{messageId} endpoint
        url = f"{GRAPH_ENDPOINT}/users/{user_id}/messages/{message_id}"
        
        # Select key email fields
        params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,hasAttachments,importance,isDraft,isRead,body,categories,flag"
        }
        
        logger.info(f"Fetching email {message_id} for user {user_id}")
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logger.warning(f"Email {message_id} not found for user {user_id}")
            return None
        else:
            logger.error(f"Graph API error fetching email: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to fetch email from Graph: {str(e)}")
        return None


def _log_email_details(email_data: Dict[str, Any], user_id: str, subscription_id: str):
    """Log email details for Phase 1 monitoring"""
    try:
        subject = email_data.get("subject", "No Subject")
        from_addr = email_data.get("from", {}).get("emailAddress", {}).get("address", "Unknown")
        received_time = email_data.get("receivedDateTime")
        has_attachments = email_data.get("hasAttachments", False)
        
        # Get body preview (first 200 characters)
        body = email_data.get("body", {})
        body_preview = body.get("content", "")[:200] if body.get("content") else "No content"
        
        logger.info("="*50)
        logger.info("EMAIL RECEIVED")
        logger.info("="*50)
        logger.info(f"User ID: {user_id}")
        logger.info(f"Subscription ID: {subscription_id}")
        logger.info(f"Subject: {subject}")
        logger.info(f"From: {from_addr}")
        logger.info(f"Received: {received_time}")
        logger.info(f"Has Attachments: {has_attachments}")
        logger.info(f"Body Preview: {body_preview}")
        logger.info("="*50)
        
    except Exception as e:
        logger.error(f"Failed to log email details: {str(e)}")


def _store_email_notification(notification_data: Dict[str, Any], email_data: Dict[str, Any]):
    """Store email notification in DynamoDB for tracking"""
    try:
        table_name = os.environ.get("EMAIL_SUBSCRIPTIONS_TABLE")
        if not table_name:
            logger.warning("EMAIL_SUBSCRIPTIONS_TABLE not configured, skipping storage")
            return
        
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        
        # Create notification record
        record = {
            "subscription_id": notification_data["subscriptionId"],
            "notification_id": str(uuid.uuid4()),
            "user_id": notification_data["userId"],
            "message_id": notification_data["messageId"],
            "change_type": notification_data["changeType"],
            "timestamp": notification_data["timestamp"],
            "email_subject": email_data.get("subject", ""),
            "email_from": email_data.get("from", {}).get("emailAddress", {}).get("address", ""),
            "email_received_time": email_data.get("receivedDateTime", ""),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "status": "processed"
        }
        
        table.put_item(Item=record)
        logger.info(f"Stored notification record: {record['notification_id']}")
        
    except Exception as e:
        logger.error(f"Failed to store notification in DynamoDB: {str(e)}")


@required_env_vars({
    "EMAIL_WEBHOOK_CLIENT_STATE": [SSMOperation.GET_PARAMETER],
    "EMAIL_SUBSCRIPTIONS_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated("post")
def create_subscription(event, context, current_user, name, data):
    """
    Create a Microsoft Graph webhook subscription for a user's inbox.
    
    Expected request body:
    {
        "userId": "user-guid-or-email", 
        "userEmail": "user@example.com"
    }
    
    Returns subscription details or error information.
    Phase 1: Manual subscription creation via API call
    Phase 2: Automated subscription management
    """
    try:
        logger.info(f"Creating email subscription for user: {current_user}")
        
        # Parse request body
        request_body = json.loads(event.get("body", "{}"))
        user_id = request_body.get("userId", "USER_ID_PLACEHOLDER")
        user_email = request_body.get("userEmail", "max.moundas@vanderbilt.edu")
        
        if not user_id or user_id == "USER_ID_PLACEHOLDER":
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "success": False,
                    "error": "userId is required. Please provide the Azure AD user GUID."
                })
            }
        
        # Create webhook subscription
        subscription_data = _create_graph_subscription(user_id, user_email)
        
        if subscription_data:
            # Store subscription in DynamoDB
            _store_subscription_record(subscription_data, user_id, user_email)
            
            logger.info(f"Successfully created subscription {subscription_data['id']} for user {user_id}")
            
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": True,
                    "data": {
                        "subscriptionId": subscription_data["id"],
                        "userId": user_id,
                        "userEmail": user_email,
                        "resource": subscription_data["resource"],
                        "expirationDateTime": subscription_data["expirationDateTime"],
                        "notificationUrl": subscription_data["notificationUrl"]
                    }
                })
            }
        else:
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "success": False,
                    "error": "Failed to create subscription"
                })
            }
            
    except Exception as e:
        logger.error(f"Create subscription error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }


def _create_graph_subscription(user_id: str, user_email: str) -> Optional[Dict[str, Any]]:
    """Create Microsoft Graph webhook subscription"""
    try:
        headers = get_graph_headers()
        
        # Get webhook endpoint URL
        api_base_url = os.environ.get("API_BASE_URL")
        stage = os.environ.get("STAGE", "dev")
        
        if not api_base_url:
            raise EmailWebhookError("API_BASE_URL not configured")
        
        notification_url = f"{api_base_url}/{stage}/integrations/email/webhook"
        client_state = os.environ.get("EMAIL_WEBHOOK_CLIENT_STATE")
        
        # Calculate expiration (72 hours from now - maximum allowed)
        expiration_time = datetime.now(timezone.utc) + timedelta(hours=SUBSCRIPTION_EXPIRATION_HOURS)
        
        # Subscription payload
        subscription_payload = {
            "changeType": "created,updated",  # Monitor new and updated emails
            "notificationUrl": notification_url,
            "resource": f"users/{user_id}/mailFolders('Inbox')/messages",
            "expirationDateTime": expiration_time.isoformat(),
            "clientState": client_state,
            "latestSupportedTlsVersion": "v1_2"
        }
        
        logger.info(f"Creating subscription for {user_email} with payload: {subscription_payload}")
        
        # Create subscription
        url = f"{GRAPH_ENDPOINT}/subscriptions"
        response = requests.post(url, headers=headers, json=subscription_payload)
        
        if response.status_code == 201:
            return response.json()
        else:
            logger.error(f"Failed to create subscription: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to create Graph subscription: {str(e)}")
        return None


def _store_subscription_record(subscription_data: Dict[str, Any], user_id: str, user_email: str):
    """Store subscription record in DynamoDB"""
    try:
        table_name = os.environ.get("EMAIL_SUBSCRIPTIONS_TABLE")
        if not table_name:
            logger.warning("EMAIL_SUBSCRIPTIONS_TABLE not configured, skipping storage")
            return
        
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        
        record = {
            "subscription_id": subscription_data["id"],
            "user_id": user_id,
            "user_email": user_email,
            "resource": subscription_data["resource"],
            "notification_url": subscription_data["notificationUrl"],
            "expiration_datetime": subscription_data["expirationDateTime"],
            "client_state": subscription_data.get("clientState", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "change_type": subscription_data.get("changeType", "created,updated")
        }
        
        table.put_item(Item=record)
        logger.info(f"Stored subscription record: {subscription_data['id']}")
        
    except Exception as e:
        logger.error(f"Failed to store subscription in DynamoDB: {str(e)}")