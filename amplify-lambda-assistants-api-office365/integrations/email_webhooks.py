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

# Conditional imports for local development compatibility
try:
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
    PRODUCTION_MODE = True
    
except ImportError:
    # Local development fallbacks
    import logging
    logger = logging.getLogger("email_webhooks")
    logger.setLevel(logging.INFO)
    
    # Create console handler if none exists
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    PRODUCTION_MODE = False
    
    # Mock classes for local development
    class DynamoDBOperation:
        PUT_ITEM = "PUT_ITEM"
        GET_ITEM = "GET_ITEM"
        
    class SSMOperation:
        GET_PARAMETER = "GET_PARAMETER"
        
    class SQSOperation:
        SEND_MESSAGE = "SEND_MESSAGE"
    
    # Mock decorators for local development
    def required_env_vars(env_vars):
        def decorator(func):
            return func
        return decorator
    
    def validated(method):
        def decorator(func):
            def wrapper(event, context, *args, **kwargs):
                # Extract current_user, name, data from args for compatibility
                return func(event, context, "local_user", "test", {})
            return wrapper
        return decorator
    
    def get_secret_parameter(param_name, prefix=""):
        """Mock secret parameter retrieval for local development"""
        logger.warning(f"Local mode: Mock secret parameter {prefix}{param_name}")
        # Return mock Microsoft Graph credentials for local testing
        if "microsoft" in param_name:
            return json.dumps({
                "client_config": {
                    "web": {
                        "client_id": "mock-client-id",
                        "client_secret": "mock-client-secret", 
                        "tenant_id": "mock-tenant-id"
                    }
                }
            })
        return "mock-secret-value"

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
    # Local development mode - return mock token
    if not PRODUCTION_MODE:
        logger.warning("Local mode: Using mock Graph API token")
        return "mock-graph-api-token-for-local-development"
    
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


def get_user_guid_from_email(user_email: str) -> Optional[str]:
    """
    Get Azure AD User GUID from email address using Microsoft Graph API.
    
    Args:
        user_email: User's email address (e.g., "max.moundas@vanderbilt.edu")
    
    Returns:
        User GUID string or None if not found
    """
    try:
        headers = get_graph_headers()
        
        # Query user by userPrincipalName (email)
        url = f"{GRAPH_ENDPOINT}/users/{user_email}"
        
        # Select only the id field for efficiency
        params = {"$select": "id,userPrincipalName,displayName"}
        
        logger.info(f"Looking up user GUID for email: {user_email}")
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            logger.info(f"Found user GUID {user_id} for {user_email}")
            return user_id
        elif response.status_code == 404:
            logger.warning(f"User not found in Azure AD: {user_email}")
            return None
        else:
            logger.error(f"Graph API error getting user: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user GUID for {user_email}: {str(e)}")
        return None


def get_all_organization_users(page_size: int = 100) -> List[Dict[str, str]]:
    """
    Get all users in the organization with their GUIDs and email addresses.
    
    Args:
        page_size: Number of users to fetch per page (max 999)
    
    Returns:
        List of user dictionaries with 'id', 'userPrincipalName', 'displayName'
    """
    try:
        headers = get_graph_headers()
        all_users = []
        
        # Start with first page
        url = f"{GRAPH_ENDPOINT}/users"
        params = {
            "$select": "id,userPrincipalName,displayName,accountEnabled",
            "$filter": "accountEnabled eq true",  # Only active users
            "$top": page_size
        }
        
        logger.info("Fetching all organization users from Azure AD")
        
        while url:
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                users = data.get("value", [])
                all_users.extend(users)
                
                # Get next page URL if it exists
                url = data.get("@odata.nextLink")
                params = None  # Next link already contains query params
                
                logger.info(f"Fetched {len(users)} users, total: {len(all_users)}")
            else:
                logger.error(f"Failed to fetch users: {response.status_code} - {response.text}")
                break
        
        logger.info(f"Retrieved {len(all_users)} total users from organization")
        return all_users
        
    except Exception as e:
        logger.error(f"Failed to get organization users: {str(e)}")
        return []


@required_env_vars({
    "EMAIL_WEBHOOK_CLIENT_STATE": [SSMOperation.GET_PARAMETER],
})
def webhook_handler(event, context):
    """
    Lightweight Microsoft Graph webhook handler.
    
    This function:
    1. Handles validation requests (GET with validationToken parameter)
    2. Forwards notification requests (POST) directly to SQS with minimal processing
    
    Microsoft Graph requires the same URL for both validation and notifications.
    This handler provides minimal overhead while forwarding to SQS for processing.
    
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
        
        # Handle notification request (POST) - forward directly to SQS
        elif event.get("httpMethod") == "POST":
            return _forward_to_sqs(event, context)
        
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


def _forward_to_sqs(event, context) -> Dict[str, Any]:
    """
    Lightweight forwarder that sends webhook notifications directly to SQS.
    Minimal processing - just forward the Microsoft Graph payload to SQS.
    """
    # Local development mode - just log and return success
    if not PRODUCTION_MODE:
        body = event.get("body", "{}")
        logger.info(f"Local mode: Would forward to SQS: {body[:200]}...")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "forwarded", "mode": "local"})
        }
    
    try:
        # Get the raw notification body from Microsoft Graph
        body = event.get("body", "{}")
        
        # Send directly to SQS with minimal processing
        queue_url = _get_sqs_queue_url()
        sqs = boto3.client("sqs")
        
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=body,  # Forward raw Microsoft Graph webhook payload
            MessageAttributes={
                "source": {
                    "StringValue": "microsoft-graph-webhook",
                    "DataType": "String"
                },
                "timestamp": {
                    "StringValue": datetime.now(timezone.utc).isoformat(),
                    "DataType": "String"
                }
            }
        )
        
        logger.info(f"Forwarded webhook notification to SQS: MessageId={response['MessageId']}")
        
        # Return success immediately
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "forwarded"})
        }
        
    except Exception as e:
        logger.error(f"Failed to forward notification to SQS: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Forwarding failed"})
        }


# Helper functions for SQS forwarding


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
    1. Receives Microsoft Graph webhook notifications from SQS (sent directly)
    2. Validates clientState for security
    3. Fetches full email content from Graph API
    4. Logs email details for Phase 1
    5. Placeholder for future AI assistant integration
    
    Handles multiple messages in a batch for efficiency.
    SQS now receives webhook notifications directly from Microsoft Graph.
    """
    try:
        logger.info(f"Email processor called with {len(event.get('Records', []))} messages")
        
        processed_count = 0
        failed_count = 0
        
        for record in event.get("Records", []):
            try:
                # Parse SQS message (contains raw Microsoft Graph webhook format)
                message_body = json.loads(record["body"])
                logger.info(f"Processing Microsoft Graph webhook notification: {message_body}")
                
                # Microsoft Graph sends notifications in a 'value' array
                notifications = message_body.get("value", [])
                if not notifications:
                    logger.warning("No notifications in Microsoft Graph webhook message")
                    processed_count += 1
                    continue
                
                # Process each notification in the webhook
                for notification in notifications:
                    # Validate required fields
                    subscription_id = notification.get("subscriptionId")
                    client_state = notification.get("clientState")
                    change_type = notification.get("changeType")
                    resource = notification.get("resource")
                    
                    if not all([subscription_id, client_state, change_type, resource]):
                        logger.error(f"Missing required fields in notification: {notification}")
                        continue
                    
                    # Validate clientState for security
                    expected_client_state = os.environ.get("EMAIL_WEBHOOK_CLIENT_STATE")
                    if client_state != expected_client_state:
                        logger.error(f"Invalid clientState: {client_state}")
                        failed_count += 1
                        continue
                    
                    # Extract message ID and user info from resource
                    # Resource format: users/{userId}/mailFolders('Inbox')/messages/{messageId}
                    if "/messages/" not in resource:
                        logger.warning(f"Unexpected resource format: {resource}")
                        continue
                        
                    # Parse user ID and message ID from resource
                    # Example: users/user@example.com/mailFolders('Inbox')/messages/AAMkAG...
                    resource_parts = resource.split("/")
                    user_id = resource_parts[1] if len(resource_parts) > 1 else None
                    message_id = resource_parts[-1] if len(resource_parts) > 0 else None
                    
                    if not user_id or not message_id:
                        logger.error(f"Could not parse user_id or message_id from resource: {resource}")
                        failed_count += 1
                        continue
                
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
                    notification_record = {
                        "subscriptionId": subscription_id,
                        "userId": user_id,
                        "messageId": message_id,
                        "changeType": change_type,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "resourceData": notification.get("resourceData", {})
                    }
                    _store_email_notification(notification_record, email_data)
                    
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
        user_id = request_body.get("userId")
        user_email = request_body.get("userEmail")
        
        # Validate inputs
        if not user_email:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "success": False,
                    "error": "userEmail is required."
                })
            }
        
        # If no userId provided, automatically get it from email address
        if not user_id:
            logger.info(f"No userId provided, looking up GUID for email: {user_email}")
            user_id = get_user_guid_from_email(user_email)
            
            if not user_id:
                return {
                    "statusCode": 404,
                    "body": json.dumps({
                        "success": False,
                        "error": f"User not found in Azure AD: {user_email}"
                    })
                }
            
            logger.info(f"Automatically found User GUID: {user_id} for {user_email}")
        
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


@required_env_vars({
    "EMAIL_SUBSCRIPTIONS_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.GET_ITEM],
})
@validated("get")
def get_user_guid_api(event, context, current_user, name, data):
    """
    API endpoint to get Azure AD User GUID from email address.
    
    GET /integrations/email/user-guid?email=user@example.com
    
    Returns:
        {
            "success": true,
            "data": {
                "userEmail": "user@example.com",
                "userId": "guid-123-456",
                "displayName": "User Name"
            }
        }
    """
    try:
        query_params = event.get("queryStringParameters") or {}
        user_email = query_params.get("email")
        
        if not user_email:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "success": False,
                    "error": "email parameter is required"
                })
            }
        
        # Get user info from Azure AD
        headers = get_graph_headers()
        url = f"{GRAPH_ENDPOINT}/users/{user_email}"
        params = {"$select": "id,userPrincipalName,displayName"}
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            user_data = response.json()
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": True,
                    "data": {
                        "userEmail": user_data.get("userPrincipalName"),
                        "userId": user_data.get("id"),
                        "displayName": user_data.get("displayName")
                    }
                })
            }
        elif response.status_code == 404:
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "success": False,
                    "error": f"User not found: {user_email}"
                })
            }
        else:
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "success": False,
                    "error": "Failed to query Azure AD"
                })
            }
            
    except Exception as e:
        logger.error(f"Get user GUID API error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }


@required_env_vars({
    "EMAIL_SUBSCRIPTIONS_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.GET_ITEM],
})
@validated("get")
def list_organization_users(event, context, current_user, name, data):
    """
    API endpoint to get all users in the organization for bulk operations.
    
    GET /integrations/email/organization/users?page_size=100
    
    Phase 2: Use this for bulk subscription creation
    """
    try:
        query_params = event.get("queryStringParameters") or {}
        page_size = int(query_params.get("page_size", 100))
        
        # Limit page size to prevent timeouts
        page_size = min(page_size, 500)
        
        users = get_all_organization_users(page_size)
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "data": {
                    "users": users,
                    "count": len(users),
                    "pageSize": page_size
                }
            })
        }
        
    except Exception as e:
        logger.error(f"List organization users API error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }


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