"""
Real-time status updates for document processing
Stores status in DynamoDB and publishes to WebSocket API for frontend updates
"""

import boto3
import json
import os
from datetime import datetime, timedelta
from pycommon.logger import getLogger

logger = getLogger("rag_status")

dynamodb = boto3.resource('dynamodb')
apigateway = None  # Lazy initialize


class DocumentStatus:
    """Status enum for document processing stages"""
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    QUEUED = "queued"
    PROCESSING_STARTED = "processing_started"
    CONVERTING_PAGES = "converting_pages"  # VDR only
    EXTRACTING_TEXT = "extracting_text"    # Text RAG only
    PROCESSING_VISUALS = "processing_visuals"
    CLASSIFYING_VISUALS = "classifying_visuals"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    EMBEDDING_PAGES = "embedding_pages"  # VDR only
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def get_status_table():
    """Get or create DynamoDB status table"""
    table_name = os.environ.get('DOCUMENT_STATUS_TABLE', 'document-processing-status')
    return dynamodb.Table(table_name)


def get_websocket_api():
    """Lazy initialize WebSocket API client"""
    global apigateway
    if apigateway is None:
        endpoint_url = os.environ.get('WEBSOCKET_API_ENDPOINT')
        if endpoint_url:
            apigateway = boto3.client(
                'apigatewaymanagementapi',
                endpoint_url=endpoint_url
            )
    return apigateway


def get_status_id(bucket, key):
    """Generate unique status ID for document"""
    return f"{bucket}#{key}"


def update_document_status(bucket, key, status, metadata=None, user=None):
    """
    Update document processing status in DynamoDB and publish to WebSocket

    Args:
        bucket: S3 bucket name
        key: S3 object key
        status: Status string from DocumentStatus enum
        metadata: Optional dict with additional info (progress, current_page, error, etc.)
        user: Optional user ID for WebSocket routing

    Returns:
        bool: True if update successful
    """
    try:
        status_id = get_status_id(bucket, key)
        timestamp = datetime.utcnow().isoformat()
        ttl = int((datetime.utcnow() + timedelta(days=1)).timestamp())  # Expire after 24h

        item = {
            'statusId': status_id,
            'bucket': bucket,
            'key': key,
            'status': status,
            'timestamp': timestamp,
            'ttl': ttl,
            'updatedAt': timestamp
        }

        if metadata:
            item['metadata'] = metadata

        if user:
            item['user'] = user

        # Store in DynamoDB
        table = get_status_table()
        table.put_item(Item=item)

        logger.info(f"Status updated: {status_id} → {status} (metadata: {metadata})")

        # Publish to WebSocket for real-time updates
        publish_to_websocket(status_id, status, metadata, user)

        return True

    except Exception as e:
        logger.error(f"Failed to update status: {str(e)}")
        return False


def get_document_status(bucket, key):
    """
    Get current status of document

    Returns:
        dict or None: Status item from DynamoDB
    """
    try:
        status_id = get_status_id(bucket, key)
        table = get_status_table()

        response = table.get_item(Key={'statusId': status_id})
        return response.get('Item')

    except Exception as e:
        logger.error(f"Failed to get status: {str(e)}")
        return None


def publish_to_websocket(status_id, status, metadata, user=None):
    """
    Publish status update to WebSocket connections

    Args:
        status_id: Document status ID
        status: Current status
        metadata: Additional metadata
        user: Optional user ID for filtering connections
    """
    try:
        api = get_websocket_api()
        if not api:
            logger.debug("WebSocket API not configured, skipping publish")
            return

        # Get active WebSocket connections for this document
        connections = get_active_connections(status_id, user)

        if not connections:
            logger.debug(f"No active connections for {status_id}")
            return

        message = {
            'type': 'document_status_update',
            'statusId': status_id,
            'status': status,
            'metadata': metadata or {},
            'timestamp': datetime.utcnow().isoformat()
        }

        message_data = json.dumps(message).encode('utf-8')

        # Send to all active connections
        for connection_id in connections:
            try:
                api.post_to_connection(
                    ConnectionId=connection_id,
                    Data=message_data
                )
                logger.debug(f"Sent update to connection {connection_id}")

            except api.exceptions.GoneException:
                # Connection no longer exists
                logger.debug(f"Connection {connection_id} is gone, removing")
                remove_connection(connection_id)

            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {str(e)}")

    except Exception as e:
        logger.error(f"Failed to publish to WebSocket: {str(e)}")


def get_active_connections(status_id, user=None):
    """
    Get list of active WebSocket connection IDs for this document

    Args:
        status_id: Document status ID
        user: Optional user ID to filter connections

    Returns:
        list: Connection IDs
    """
    try:
        # Get connections from DynamoDB connections table
        connections_table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE', 'websocket-connections')
        connections_table = dynamodb.Table(connections_table_name)

        # Query connections subscribed to this status_id
        response = connections_table.query(
            IndexName='StatusIdIndex',  # GSI on statusId
            KeyConditionExpression='statusId = :sid',
            ExpressionAttributeValues={':sid': status_id}
        )

        connections = []
        for item in response.get('Items', []):
            # Filter by user if specified
            if user and item.get('user') != user:
                continue

            connections.append(item['connectionId'])

        return connections

    except Exception as e:
        logger.error(f"Failed to get active connections: {str(e)}")
        return []


def remove_connection(connection_id):
    """
    Remove stale WebSocket connection from DynamoDB

    Args:
        connection_id: WebSocket connection ID
    """
    try:
        connections_table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE', 'websocket-connections')
        connections_table = dynamodb.Table(connections_table_name)

        connections_table.delete_item(Key={'connectionId': connection_id})
        logger.debug(f"Removed connection {connection_id}")

    except Exception as e:
        logger.error(f"Failed to remove connection: {str(e)}")


def update_progress(bucket, key, progress, stage=None, details=None):
    """
    Convenience method to update progress percentage

    Args:
        bucket: S3 bucket
        key: S3 key
        progress: Progress percentage (0-100)
        stage: Optional status stage
        details: Optional additional details dict
    """
    metadata = {'progress': progress}

    if details:
        metadata.update(details)

    # Fix: Handle None return value from get_document_status
    if stage:
        status = stage
    else:
        current_status = get_document_status(bucket, key)
        status = current_status.get('status', DocumentStatus.PROCESSING_STARTED) if current_status else DocumentStatus.PROCESSING_STARTED

    update_document_status(bucket, key, status, metadata)


def mark_failed(bucket, key, error_message, stage=None):
    """
    Mark document processing as failed

    Args:
        bucket: S3 bucket
        key: S3 key
        error_message: Error description
        stage: Optional stage where failure occurred
    """
    metadata = {
        'error': error_message,
        'failed_at_stage': stage or 'unknown'
    }

    update_document_status(bucket, key, DocumentStatus.FAILED, metadata)


def mark_completed(bucket, key, result_metadata=None):
    """
    Mark document processing as completed

    Args:
        bucket: S3 bucket
        key: S3 key
        result_metadata: Optional dict with completion details
    """
    metadata = result_metadata or {}
    metadata['progress'] = 100

    update_document_status(bucket, key, DocumentStatus.COMPLETED, metadata)
