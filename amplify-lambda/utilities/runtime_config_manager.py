"""
Runtime Configuration Manager

Central Lambda function that handles API Gateway timeout configuration.
Called by CloudFormation custom resources from other services during deployment.
"""

import boto3
import json
import logging
import os
from typing import Dict, Optional, List

logger = logging.getLogger()
logger.setLevel(logging.INFO)

apigateway = boto3.client('apigateway')

# In-memory cache (persists across warm invocations)
_api_gateway_cache = {}


def find_resource_by_path(api_id: str, target_path: str) -> Optional[str]:
    """
    Find API Gateway resource ID by path.

    Args:
        api_id: API Gateway REST API ID
        target_path: Resource path (e.g., '/integrations/user/files/upload')

    Returns:
        Resource ID or None if not found
    """
    try:
        # Normalize path (ensure leading slash)
        if not target_path.startswith('/'):
            target_path = '/' + target_path

        paginator = apigateway.get_paginator('get_resources')

        for page in paginator.paginate(restApiId=api_id):
            for resource in page.get('items', []):
                if resource.get('path') == target_path:
                    logger.info(f"Found resource: {target_path} -> {resource['id']}")
                    return resource['id']

        logger.warning(f"Resource not found: {target_path}")
        return None

    except Exception as e:
        logger.error(f"Error finding resource: {e}")
        return None


def extend_api_gateway_timeout(
    api_id: str,
    resource_path: str,
    method: str,
    lambda_timeout: int,
    function_name: str,
    max_timeout_override: int = None
) -> Dict:
    """
    Check and extend API Gateway integration timeout if needed.

    Args:
        api_id: API Gateway REST API ID
        resource_path: API Gateway resource path (e.g., '/chat_stream')
        method: HTTP method (e.g., 'POST')
        lambda_timeout: Lambda function timeout in seconds
        function_name: Lambda function name (for logging)
        max_timeout_override: Override max timeout for streaming endpoints (from env var)

    Returns:
        Dict with status, message, and details
    """

    cache_key = f"{api_id}:{resource_path}:{method}"

    # Check if already processed in this invocation
    if _api_gateway_cache.get(cache_key):
        return {
            'status': 'cached',
            'message': 'Already configured in this invocation',
            'function_name': function_name
        }

    try:
        # Find resource by path
        resource_id = find_resource_by_path(api_id, resource_path)
        if not resource_id:
            return {
                'status': 'error',
                'message': f'Resource not found: {resource_path}',
                'function_name': function_name
            }

        # Get current integration
        try:
            integration = apigateway.get_integration(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod=method.upper()
            )
            current_timeout = integration.get('timeoutInMillis', 29000)

            # Simply use the max_timeout_override for all endpoints
            # AWS will reject if the value exceeds what's allowed for the endpoint type
            target_timeout_ms = max_timeout_override

            logger.info(
                f"Setting {function_name} to timeout: {target_timeout_ms}ms "
                f"(Lambda: {lambda_timeout}s, Current: {current_timeout}ms)"
            )

        except Exception as e:
            logger.error(f"Error getting integration: {e}")
            return {
                'status': 'error',
                'message': f'Integration not found for {method} {resource_path}',
                'function_name': function_name
            }

        # Check if already at target timeout
        if current_timeout >= target_timeout_ms:
            logger.info(
                f"Timeout already at {current_timeout}ms for {function_name} "
                f"({method} {resource_path}), target is {target_timeout_ms}ms"
            )
            _api_gateway_cache[cache_key] = True
            return {
                'status': 'ok',
                'message': f'Already at {current_timeout}ms (target: {target_timeout_ms}ms)',
                'function_name': function_name,
                'current_timeout_ms': current_timeout
            }

        # Extend timeout to target (TESTING: Always update, even if already set)
        apigateway.update_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=method.upper(),
            patchOperations=[
                {
                    'op': 'replace',
                    'path': '/timeoutInMillis',
                    'value': str(target_timeout_ms)
                }
            ]
        )

        logger.info(
            f"✓ Extended timeout to {target_timeout_ms}ms for {function_name} "
            f"({method} {resource_path}, was {current_timeout}ms)"
        )

        _api_gateway_cache[cache_key] = True

        return {
            'status': 'extended',
            'message': f'Timeout extended to {target_timeout_ms}ms',
            'function_name': function_name,
            'previous_timeout_ms': current_timeout,
            'new_timeout_ms': target_timeout_ms,
            'resource_id': resource_id
        }

    except Exception as e:
        error_msg = f"Error extending timeout: {str(e)}"
        logger.error(error_msg)
        return {
            'status': 'error',
            'message': error_msg,
            'function_name': function_name
        }


def handle_cloudformation_request(event: Dict, context) -> Dict:
    """
    Handle CloudFormation custom resource create/update request.

    Expected event format:
    {
      "RequestType": "Create" | "Update" | "Delete",
      "ResourceProperties": {
        "ApiGatewayId": "abc123",
        "Functions": [
          {
            "name": "myFunction",
            "path": "/my/path",
            "method": "POST",
            "timeout": 300
          }
        ]
      }
    }
    """

    request_type = event.get('RequestType')
    properties = event.get('ResourceProperties', {})

    # On delete, do nothing (keep timeouts as-is)
    if request_type == 'Delete':
        logger.info("Delete request - no action needed")
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': 'Delete operation - no action taken'
        })

    # Check if API Gateway timeout extension is enabled
    max_timeout_env = os.environ.get('API_GATEWAY_MAX_TIMEOUT_MS', '').strip()
    stage = os.environ.get('STAGE', 'unknown')
    if not max_timeout_env:
        logger.info("API_GATEWAY_MAX_TIMEOUT_MS not configured - skipping timeout extension")
        logger.info(f"To enable: Set /amplify/{stage}/API_GATEWAY_MAX_TIMEOUT_MS in Parameter Store to '180000'")
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': 'API Gateway timeout extension disabled (API_GATEWAY_MAX_TIMEOUT_MS not set)'
        })

    try:
        max_timeout_override = int(max_timeout_env)

        # Validate timeout meets AWS API Gateway minimum
        # Minimum: 50ms (AWS hard limit)
        # Maximum: No cap here - depends on what AWS approved for your account
        if max_timeout_override < 50:
            logger.warning(f"API_GATEWAY_MAX_TIMEOUT_MS too low ({max_timeout_override}ms), minimum is 50ms - skipping")
            return send_cfn_response(event, context, 'SUCCESS', {
                'Message': f'API Gateway timeout extension disabled (value too low: {max_timeout_override}ms, min: 50ms)'
            })

        logger.info(f"API Gateway timeout extension enabled with max override: {max_timeout_override}ms")

    except ValueError:
        logger.warning(f"Invalid API_GATEWAY_MAX_TIMEOUT_MS value: '{max_timeout_env}' (must be integer) - skipping")
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': f'API Gateway timeout extension disabled (invalid value: {max_timeout_env})'
        })

    # Get parameters
    api_id = properties.get('ApiGatewayId')
    functions = properties.get('Functions', [])

    if not api_id:
        error_msg = 'ApiGatewayId not provided'
        logger.error(error_msg)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })

    if not functions:
        logger.warning('No functions specified')
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': 'No functions to configure'
        })

    # Process each function
    results = []
    for func in functions:
        # Ensure timeout is an integer (CloudFormation may pass as string)
        timeout_value = func.get('timeout', 6)
        lambda_timeout = int(timeout_value) if isinstance(timeout_value, str) else timeout_value

        result = extend_api_gateway_timeout(
            api_id=api_id,
            resource_path=func.get('path', ''),
            method=func.get('method', 'GET'),
            lambda_timeout=lambda_timeout,
            function_name=func.get('name', 'unknown'),
            max_timeout_override=max_timeout_override
        )
        results.append(result)

    # Check for failures
    failed = [r for r in results if r['status'] == 'error']

    if failed:
        logger.error(f"Failed to configure {len(failed)} functions: {failed}")
        return send_cfn_response(event, context, 'FAILED', {
            'Error': f'Failed to configure {len(failed)} functions',
            'Results': results
        })

    # Success
    successful = [r for r in results if r['status'] == 'extended']
    skipped = [r for r in results if r['status'] == 'skipped']
    already_ok = [r for r in results if r['status'] == 'ok']

    logger.info(
        f"Configuration complete: {len(successful)} extended, "
        f"{len(already_ok)} already ok, {len(skipped)} skipped"
    )

    return send_cfn_response(event, context, 'SUCCESS', {
        'Message': f'Configured {len(functions)} functions',
        'Extended': len(successful),
        'AlreadyOk': len(already_ok),
        'Skipped': len(skipped),
        'Results': results
    })


def send_cfn_response(event: Dict, context, status: str, response_data: Dict) -> Dict:
    """
    Send response to CloudFormation.

    Args:
        event: CloudFormation event
        context: Lambda context
        status: 'SUCCESS' or 'FAILED'
        response_data: Data to return to CloudFormation
    """
    import urllib3

    response_body = json.dumps({
        'Status': status,
        'Reason': f"See CloudWatch Log Stream: {context.log_stream_name}",
        'PhysicalResourceId': context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': response_data
    })

    logger.info(f"Sending CloudFormation response: {status}")

    try:
        http = urllib3.PoolManager()
        response = http.request(
            'PUT',
            event['ResponseURL'],
            body=response_body,
            headers={'Content-Type': 'application/json'}
        )
        logger.info(f"CloudFormation response sent: {response.status}")
    except Exception as e:
        logger.error(f"Error sending CloudFormation response: {e}")

    return {'statusCode': 200}


def lambda_handler(event, context):
    """
    Main Lambda handler.

    Handles CloudFormation custom resource requests to configure
    API Gateway integration timeouts.
    """

    logger.info(f"Received event: {json.dumps(event, default=str)}")

    try:
        # Check if this is a CloudFormation custom resource
        if 'RequestType' in event:
            return handle_cloudformation_request(event, context)

        else:
            # Direct invocation (for testing)
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Direct invocation not supported. Use CloudFormation custom resource.'
                })
            }

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)

        # If CloudFormation request, send failure response
        if 'RequestType' in event:
            return send_cfn_response(event, context, 'FAILED', {
                'Error': str(e)
            })

        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
