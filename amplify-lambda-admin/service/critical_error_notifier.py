"""
critical_error_notifier.py

Lambda function that listens to DynamoDB Streams on the CriticalErrorsTable
and sends SNS notifications when new critical errors are inserted.

This function is triggered automatically by DynamoDB Streams (INSERT events only)
and publishes formatted notifications to an SNS topic for admin alerts.

Copyright (c) 2025 Vanderbilt University
Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
"""

import json
import os
import boto3
from datetime import datetime
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

# Initialize SNS and STS clients
sns = boto3.client('sns')
sts = boto3.client('sts')

def get_sns_topic_arn(topic_name: str) -> str:
    """
    Construct SNS topic ARN from topic name using AWS account ID and region.
    
    Args:
        topic_name: Name of the SNS topic
    
    Returns:
        str: Full ARN of the SNS topic
    """
    account_id = sts.get_caller_identity()["Account"]
    region = sns.meta.region_name
    return f"arn:aws:sns:{region}:{account_id}:{topic_name}"

@required_env_vars({
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution(operation_name="notify_critical_error", account="system")
def notify_critical_error(event: dict, context) -> dict:
    """
    Lambda handler for DynamoDB Stream events on CriticalErrorsTable.
    
    Triggered automatically when new errors are inserted into the table.
    Formats and sends SNS notifications for critical errors.
    
    Args:
        event: DynamoDB Stream event containing Records
        context: Lambda context object
    
    Returns:
        dict: Response with success status and count of notifications sent
    
    Environment Variables:
        CRITICAL_ERRORS_SNS_TOPIC_NAME: Name of the SNS topic for notifications
    """
    
    topic_name = os.environ.get('CRITICAL_ERRORS_SNS_TOPIC_NAME')
    
    if not topic_name:
        print("ERROR: CRITICAL_ERRORS_SNS_TOPIC_NAME environment variable not set")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'SNS topic name not configured'})
        }
    
    # Construct the full ARN from the topic name
    topic_arn = get_sns_topic_arn(topic_name)
    
    notifications_sent = 0
    
    try:
        # Process each record from the DynamoDB Stream
        for record in event.get('Records', []):
            event_name = record['eventName']
            
            # Process INSERT (new errors) and MODIFY (status changes, occurrence increases)
            if event_name not in ['INSERT', 'MODIFY']:
                continue
            
            # Extract the new error data from DynamoDB format
            new_image = record['dynamodb'].get('NewImage', {})
            
            if not new_image:
                continue
            
            # Parse DynamoDB types to native Python types
            error_data = _parse_dynamodb_item(new_image)
            
            # For INSERT events, always send notification (first time error appears)
            should_notify = event_name == 'INSERT'
            
            # For MODIFY events, ONLY notify if status changed to RETURNED
            # This prevents email spam from recurring errors
            if event_name == 'MODIFY':
                old_image = record['dynamodb'].get('OldImage', {})
                if old_image:
                    old_data = _parse_dynamodb_item(old_image)
                    old_status = old_data.get('status', '')
                    new_status = error_data.get('status', '')
                    
                    # Only notify if status changed to RETURNED (error came back after being resolved)
                    if new_status == 'RETURNED' and old_status != 'RETURNED':
                        should_notify = True
                        print(f"⚠️ RETURNED ERROR - Sending notification for error_id: {error_data.get('error_id')}")
                    else:
                        # Occurrence count increased but status not RETURNED - no notification
                        # This prevents email spam from recurring errors
                        should_notify = False
            
            if not should_notify:
                continue
            
            # Format and send notification
            is_returned = error_data.get('status') == 'RETURNED'
            message = _format_notification_message(error_data, is_returned=is_returned)
            subject = _format_notification_subject(error_data, is_returned=is_returned)
            
            # Publish to SNS
            sns.publish(
                TopicArn=topic_arn,
                Subject=subject,
                Message=message
            )
            
            notifications_sent += 1
            
            print(f"Notification sent for error_id: {error_data.get('error_id')}, event: {event_name}")
    
    except Exception as e:
        print(f"Error processing DynamoDB Stream event: {str(e)}")
        print(f"Event: {json.dumps(event)}")
        # Don't raise - we don't want to block the stream
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'notifications_sent': notifications_sent
            })
        }
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f'Successfully sent {notifications_sent} notification(s)',
            'notifications_sent': notifications_sent
        })
    }


def _parse_dynamodb_item(dynamodb_item: dict) -> dict:
    """
    Convert DynamoDB JSON format to regular Python dict.
    
    DynamoDB format: {'field': {'S': 'value'}} or {'field': {'N': '123'}}
    Python format: {'field': 'value'} or {'field': 123}
    
    Args:
        dynamodb_item: DynamoDB formatted item
    
    Returns:
        dict: Python native types
    """
    result = {}
    
    for key, value in dynamodb_item.items():
        if 'S' in value:  # String
            result[key] = value['S']
        elif 'N' in value:  # Number
            result[key] = int(value['N']) if '.' not in value['N'] else float(value['N'])
        elif 'BOOL' in value:  # Boolean
            result[key] = value['BOOL']
        elif 'M' in value:  # Map (nested object)
            result[key] = _parse_dynamodb_item(value['M'])
        elif 'L' in value:  # List
            result[key] = [_parse_dynamodb_value(item) for item in value['L']]
        elif 'NULL' in value:  # Null
            result[key] = None
    
    return result


def _parse_dynamodb_value(value: dict):
    """Parse a single DynamoDB value."""
    if 'S' in value:
        return value['S']
    elif 'N' in value:
        return int(value['N']) if '.' not in value['N'] else float(value['N'])
    elif 'BOOL' in value:
        return value['BOOL']
    elif 'M' in value:
        return _parse_dynamodb_item(value['M'])
    elif 'L' in value:
        return [_parse_dynamodb_value(item) for item in value['L']]
    elif 'NULL' in value:
        return None
    return None


def _format_notification_subject(error_data: dict, is_returned: bool = False) -> str:
    """
    Format the email subject line for the notification.
    
    Args:
        error_data: Parsed error data
        is_returned: Whether this is a returned error (was resolved, came back)
    
    Returns:
        str: Email subject line
    """
    severity = error_data.get('severity', 'UNKNOWN')
    service_name = error_data.get('service_name', 'Unknown Service')
    error_type = error_data.get('error_type', 'Unknown Error')
    
    # Emoji based on severity
    emoji = {
        'CRITICAL': '🚨',
        'HIGH': '⚠️',
        'MEDIUM': '⚡',
        'LOW': 'ℹ️'
    }.get(severity, '❗')
    
    # Add RETURNED indicator if error came back after resolution
    prefix = "RETURNED ERROR" if is_returned else severity
    
    return f"{emoji} {prefix}: {error_type} in {service_name}"


def _format_notification_message(error_data: dict, is_returned: bool = False) -> str:
    """
    Format the notification message body with error details.
    
    Args:
        error_data: Parsed error data
        is_returned: Whether this is a returned error (was resolved, came back)
    
    Returns:
        str: Formatted message string
    """
    # Extract fields with defaults
    error_id = error_data.get('error_id', 'N/A')
    severity = error_data.get('severity', 'UNKNOWN')
    service_name = error_data.get('service_name', 'Unknown')
    function_name = error_data.get('function_name', 'Unknown')
    error_type = error_data.get('error_type', 'Unknown Error')
    error_message = error_data.get('error_message', 'No message provided')
    current_user = error_data.get('current_user', 'system')
    timestamp = error_data.get('timestamp', 0)
    created_at = error_data.get('created_at', 'Unknown')
    occurrence_count = error_data.get('occurrence_count', 1)
    affected_users = error_data.get('affected_users', {})
    unique_user_count = error_data.get('unique_user_count', len(affected_users) if isinstance(affected_users, dict) else 0)
    
    # Format timestamp
    if timestamp:
        try:
            dt = datetime.fromtimestamp(timestamp)
            formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
        except:
            formatted_time = created_at
    else:
        formatted_time = created_at
    
    # Build message header
    header = "🔴 ERROR RETURNED AFTER RESOLUTION 🔴" if is_returned else "🚨 CRITICAL ERROR DETECTED 🚨"
    
    message_lines = [
        "=" * 70,
        header,
        "=" * 70,
        "",
        f"Error ID: {error_id}",
        f"Severity: {severity}",
        f"Time: {formatted_time}",
        f"Occurrences: {occurrence_count}",
        f"Unique Users Affected: {unique_user_count}",
        "",
    ]
    
    # Add returned error notice
    if is_returned:
        resolution_history = error_data.get('resolution_history', [])
        if resolution_history:
            last_resolution = resolution_history[-1]
            message_lines.extend([
                "⚠️ WARNING: This error was previously resolved but has returned!",
                f"Last resolved by: {last_resolution.get('resolved_by', 'Unknown')}",
                f"Previous resolution notes: {last_resolution.get('resolution_notes', 'None provided')}",
                ""
            ])
    
    message_lines.extend([
        "--- ERROR DETAILS ---",
        f"Service: {service_name}",
        f"Function: {function_name}",
        f"Error Type: {error_type}",
        f"User: {current_user}",
        "",
        "--- ERROR MESSAGE ---",
        error_message,
        ""
    ])
    
    # Add stack trace if available
    stack_trace = error_data.get('stack_trace')
    if stack_trace:
        message_lines.extend([
            "--- STACK TRACE ---",
            stack_trace[:1000],  # Limit stack trace length
            ""
        ])
    
    # Add context if available
    context = error_data.get('context')
    if context:
        message_lines.extend([
            "--- ADDITIONAL CONTEXT ---",
            json.dumps(context, indent=2),
            ""
        ])
    
    # Add footer
    api_base = os.environ.get('API_BASE_URL', 'your-api-url')
    message_lines.extend([
        "=" * 70,
        f"View in Admin Dashboard: https://{api_base}/admin/errors/{error_id}",
        "",
        "This is an automated notification from the Amplify Error Tracking System.",
        "=" * 70
    ])
    
    return "\n".join(message_lines)
