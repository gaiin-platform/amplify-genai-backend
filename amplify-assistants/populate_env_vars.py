"""
Parameter Store Auto-Populator for amplify-assistants service.

Custom Lambda function that automatically populates AWS Parameter Store
with locally defined environment variables from serverless.yml.
Called by CloudFormation custom resources during deployment.

This Lambda reads its OWN environment variables and syncs any that match
the locally-defined pattern (${service}-${stage}-*) to Parameter Store.

Uses shared logic from pycommon.deployment.parameter_store_sync.
"""

import json
import logging
import os
import urllib3
from pycommon.deployment.parameter_store_sync import populate_parameters

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Main Lambda handler for CloudFormation custom resource.

    Expected event format:
    {
      "RequestType": "Create" | "Update" | "Delete",
      "ResourceProperties": {
        "ServiceName": "amplify-assistants",
        "Stage": "dev"
      }
    }
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


def handle_cloudformation_request(event, context):
    """
    Handle CloudFormation custom resource create/update request.

    This Lambda has access to ALL environment variables from serverless.yml.
    We simply check our own os.environ and sync locally-defined vars to Parameter Store!
    """
    request_type = event.get('RequestType')

    # On delete, do nothing (keep parameters as-is)
    if request_type == 'Delete':
        logger.info("Delete request - no action needed")
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': 'Delete operation - no action taken'
        })

    # Get service_name and stage from environment
    service_name = os.environ.get('SERVICE_NAME')
    stage = os.environ.get('STAGE')

    if not service_name:
        error_msg = 'ServiceName not available (missing SERVICE_NAME env var)'
        logger.error(error_msg)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })

    if not stage:
        error_msg = 'Stage not available (missing STAGE env var)'
        logger.error(error_msg)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })

    # Use this Lambda's own environment variables!
    # All locally-defined vars are already here with resolved values
    logger.info(
        f"Using Lambda's own environment variables (total: {len(os.environ)})"
    )
    env_vars = dict(os.environ)

    # Populate parameters using pycommon shared logic
    try:
        results = populate_parameters(service_name, stage, env_vars)

        # Check for errors
        if results['errors'] > 0:
            logger.warning(
                f"Some parameters failed to populate: {results['errors']}"
            )
            # Still return SUCCESS because partial failure shouldn't block
            # deployment
            return send_cfn_response(event, context, 'SUCCESS', {
                'Message': f"Populated with {results['errors']} errors",
                'Results': results
            })

        # Success
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': f"Populated {results['totalVariables']} variables",
            'Results': results
        })

    except Exception as e:
        error_msg = f"Error populating parameters: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })


def send_cfn_response(event, context, status, response_data):
    """
    Send response to CloudFormation.

    Args:
        event: CloudFormation event
        context: Lambda context
        status: 'SUCCESS' or 'FAILED'
        response_data: Data to return to CloudFormation
    """
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
