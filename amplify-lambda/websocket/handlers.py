"""
WebSocket API Handlers for Real-Time Document Status Updates

Provides WebSocket connection management for frontend to receive
real-time updates on document processing status
"""

import boto3
import json
import os
from datetime import datetime
from pycommon.logger import getLogger

logger = getLogger("websocket_handlers")

dynamodb = boto3.resource('dynamodb')


def get_connections_table():
    """Get WebSocket connections DynamoDB table"""
    table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE', 'websocket-connections')
    return dynamodb.Table(table_name)


def connect(event, context):
    """
    Handle WebSocket $connect route

    Registers new WebSocket connection in DynamoDB

    Frontend connects with:
    wss://{api-id}.execute-api.{region}.amazonaws.com/{stage}?user={userId}
    """
    try:
        connection_id = event['requestContext']['connectionId']
        query_params = event.get('queryStringParameters', {}) or {}
        user_id = query_params.get('user')

        logger.info(f"WebSocket connection request: {connection_id}, user: {user_id}")

        # Store connection in DynamoDB
        table = get_connections_table()

        table.put_item(Item={
            'connectionId': connection_id,
            'user': user_id,
            'connectedAt': datetime.utcnow().isoformat(),
            'ttl': int(datetime.utcnow().timestamp()) + 86400  # Expire after 24h
        })

        logger.info(f"Connection {connection_id} registered for user {user_id}")

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Connected'})
        }

    except Exception as e:
        logger.error(f"Connection failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def disconnect(event, context):
    """
    Handle WebSocket $disconnect route

    Removes connection from DynamoDB when client disconnects
    """
    try:
        connection_id = event['requestContext']['connectionId']

        logger.info(f"WebSocket disconnection: {connection_id}")

        # Remove connection from DynamoDB
        table = get_connections_table()

        table.delete_item(Key={'connectionId': connection_id})

        logger.info(f"Connection {connection_id} removed")

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Disconnected'})
        }

    except Exception as e:
        logger.error(f"Disconnection failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def subscribe(event, context):
    """
    Handle subscribe action

    Client sends:
    {
        "action": "subscribe",
        "statusId": "bucket#key"
    }

    This subscribes the connection to updates for a specific document
    """
    try:
        connection_id = event['requestContext']['connectionId']

        # Parse message body
        body = json.loads(event.get('body', '{}'))
        status_id = body.get('statusId')

        if not status_id:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'statusId is required'})
            }

        logger.info(f"Subscribe request: {connection_id} → {status_id}")

        # Update connection with statusId
        table = get_connections_table()

        table.update_item(
            Key={'connectionId': connection_id},
            UpdateExpression='SET statusId = :sid, subscribedAt = :ts',
            ExpressionAttributeValues={
                ':sid': status_id,
                ':ts': datetime.utcnow().isoformat()
            }
        )

        logger.info(f"Connection {connection_id} subscribed to {status_id}")

        # Send confirmation message back to client
        send_to_connection(
            connection_id,
            event['requestContext'],
            {
                'type': 'subscription_confirmed',
                'statusId': status_id,
                'message': f'Subscribed to updates for {status_id}'
            }
        )

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Subscribed'})
        }

    except Exception as e:
        logger.error(f"Subscribe failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def unsubscribe(event, context):
    """
    Handle unsubscribe action

    Client sends:
    {
        "action": "unsubscribe"
    }

    Removes subscription from connection
    """
    try:
        connection_id = event['requestContext']['connectionId']

        logger.info(f"Unsubscribe request: {connection_id}")

        # Remove statusId from connection
        table = get_connections_table()

        table.update_item(
            Key={'connectionId': connection_id},
            UpdateExpression='REMOVE statusId, subscribedAt'
        )

        logger.info(f"Connection {connection_id} unsubscribed")

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Unsubscribed'})
        }

    except Exception as e:
        logger.error(f"Unsubscribe failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def default_handler(event, context):
    """
    Handle $default route (catch-all for unknown actions)

    Client sends:
    {
        "action": "ping"
    }

    Responds with pong to keep connection alive
    """
    try:
        connection_id = event['requestContext']['connectionId']

        # Parse message body
        body = json.loads(event.get('body', '{}'))
        action = body.get('action', 'unknown')

        logger.info(f"Default handler: {connection_id}, action: {action}")

        # Handle ping
        if action == 'ping':
            send_to_connection(
                connection_id,
                event['requestContext'],
                {
                    'type': 'pong',
                    'timestamp': datetime.utcnow().isoformat()
                }
            )

            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'pong'})
            }

        # Unknown action
        logger.warning(f"Unknown action: {action}")

        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Unknown action: {action}'})
        }

    except Exception as e:
        logger.error(f"Default handler failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def send_to_connection(connection_id, request_context, message):
    """
    Send message to WebSocket connection

    Args:
        connection_id: WebSocket connection ID
        request_context: Lambda request context (contains API Gateway info)
        message: Dict to send as JSON

    Returns:
        bool: True if successful
    """
    try:
        # Get API Gateway Management API endpoint
        domain_name = request_context['domainName']
        stage = request_context['stage']
        endpoint_url = f"https://{domain_name}/{stage}"

        # Create API Gateway Management API client
        apigateway = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=endpoint_url
        )

        # Send message
        message_data = json.dumps(message).encode('utf-8')

        apigateway.post_to_connection(
            ConnectionId=connection_id,
            Data=message_data
        )

        logger.debug(f"Sent message to {connection_id}")

        return True

    except apigateway.exceptions.GoneException:
        # Connection no longer exists
        logger.warning(f"Connection {connection_id} is gone")

        # Remove from DynamoDB
        try:
            table = get_connections_table()
            table.delete_item(Key={'connectionId': connection_id})
        except:
            pass

        return False

    except Exception as e:
        logger.error(f"Failed to send to connection {connection_id}: {str(e)}")
        return False


def broadcast_to_status_subscribers(status_id, message):
    """
    Broadcast message to all connections subscribed to a status_id

    Used by status_manager.py to publish status updates

    Args:
        status_id: Document status ID (bucket#key)
        message: Dict to send as JSON

    Returns:
        int: Number of connections notified
    """
    try:
        # Get all connections subscribed to this status_id
        table = get_connections_table()

        response = table.query(
            IndexName='StatusIdIndex',  # GSI on statusId
            KeyConditionExpression='statusId = :sid',
            ExpressionAttributeValues={':sid': status_id}
        )

        connections = response.get('Items', [])

        if not connections:
            logger.debug(f"No subscribers for {status_id}")
            return 0

        logger.info(f"Broadcasting to {len(connections)} subscribers of {status_id}")

        # Get WebSocket API endpoint from environment
        endpoint_url = os.environ.get('WEBSOCKET_API_ENDPOINT')

        if not endpoint_url:
            logger.error("WEBSOCKET_API_ENDPOINT not configured")
            return 0

        # Create API Gateway Management API client
        apigateway = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=endpoint_url
        )

        message_data = json.dumps(message).encode('utf-8')

        sent_count = 0

        # Send to each connection
        for conn in connections:
            connection_id = conn['connectionId']

            try:
                apigateway.post_to_connection(
                    ConnectionId=connection_id,
                    Data=message_data
                )

                sent_count += 1
                logger.debug(f"Sent to {connection_id}")

            except apigateway.exceptions.GoneException:
                # Connection gone, remove from DynamoDB
                logger.debug(f"Connection {connection_id} is gone, removing")

                try:
                    table.delete_item(Key={'connectionId': connection_id})
                except:
                    pass

            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {str(e)}")

        logger.info(f"Broadcast complete: {sent_count}/{len(connections)} delivered")

        return sent_count

    except Exception as e:
        logger.error(f"Broadcast failed: {str(e)}")
        return 0
