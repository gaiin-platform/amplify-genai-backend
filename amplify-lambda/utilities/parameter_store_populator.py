"""
Parameter Store Auto-Populator

Custom Lambda function that automatically populates AWS Parameter Store
with locally defined environment variables from serverless.yml.
Called by CloudFormation custom resources during deployment.

This Lambda reads its OWN environment variables and syncs any that match
the locally-defined pattern (${service}-${stage}-*) to Parameter Store.

Simple, safe, and efficient!
"""

import boto3
import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm_client = boto3.client('ssm')


def extract_locally_defined_vars(env_vars: Dict[str, str], service_name: str, stage: str) -> Dict[str, str]:
    """
    Extract locally-defined variables by checking if their VALUES match the pattern:
    ${service_name}-${stage}-*

    This is SAFE because:
    1. Only syncs vars whose VALUES start with the service-stage prefix
    2. Won't sync imported SSM parameters (they're paths like /amplify/...)
    3. Won't sync AWS-managed vars (like AWS_REGION, AWS_LAMBDA_FUNCTION_NAME, etc.)
    4. Automatic - no hardcoded list to maintain

    Example:
        POLL_STATUS_TABLE = "amplify-v6-lambda-dev-poll-status"  ✓ SYNC
        API_KEYS_DYNAMODB_TABLE = "/amplify/dev/amplify-v6-lambda-object-access/API_KEYS_DYNAMODB_TABLE"  ✗ SKIP (imported)
        AWS_REGION = "us-east-1"  ✗ SKIP (AWS-managed)
        AWS_LAMBDA_FUNCTION_NAME = "amplify-v6-lambda-dev-xxx"  ✗ SKIP (AWS-managed, even though value matches pattern)

    Args:
        env_vars: Environment variables from Lambda function
        service_name: Service name (e.g., "amplify-v6-lambda")
        stage: Deployment stage (e.g., "dev")

    Returns:
        Dict of locally-defined variable names to their resolved values
    """
    # AWS-managed environment variables to exclude
    AWS_MANAGED_VARS = {
        'AWS_LAMBDA_FUNCTION_NAME',
        'AWS_LAMBDA_FUNCTION_VERSION',
        'AWS_LAMBDA_FUNCTION_MEMORY_SIZE',
        'AWS_LAMBDA_LOG_GROUP_NAME',
        'AWS_LAMBDA_LOG_STREAM_NAME',
        'AWS_REGION',
        'AWS_DEFAULT_REGION',
        'AWS_EXECUTION_ENV',
        'AWS_LAMBDA_RUNTIME_API',
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_SESSION_TOKEN',
        'TZ',
        'LAMBDA_TASK_ROOT',
        'LAMBDA_RUNTIME_DIR',
        '_HANDLER',
        '_X_AMZN_TRACE_ID',
    }

    locally_defined = {}
    prefix = f"{service_name}-{stage}-"

    logger.info(f"Looking for locally-defined variables with prefix: {prefix}")

    for var_name, var_value in env_vars.items():
        # Skip AWS-managed variables
        if var_name in AWS_MANAGED_VARS or var_name.startswith('AWS_'):
            logger.debug(f"✗ Skipping AWS-managed variable: {var_name}")
            continue

        # Skip empty values
        if not var_value:
            continue

        # Check if value starts with our service-stage prefix
        if var_value.startswith(prefix):
            locally_defined[var_name] = var_value
            logger.info(f"✓ Found locally defined variable: {var_name} = {var_value}")
        else:
            # Log skipped variables for debugging
            logger.debug(f"✗ Skipping non-local variable: {var_name} = {var_value[:50]}...")

    logger.info(f"Extracted {len(locally_defined)} locally-defined variables")
    return locally_defined


def create_or_update_parameter(
    parameter_name: str,
    value: str,
    description: str
) -> Dict[str, str]:
    """
    Create or update a parameter in AWS Parameter Store.

    Args:
        parameter_name: Full parameter path (e.g., /amplify/dev/service/VAR_NAME)
        value: Parameter value
        description: Parameter description

    Returns:
        Dict with status and message
    """
    try:
        # Check if parameter exists
        try:
            existing = ssm_client.get_parameter(Name=parameter_name)
            existing_value = existing['Parameter']['Value']

            # Update if different
            if existing_value != value:
                ssm_client.put_parameter(
                    Name=parameter_name,
                    Value=value,
                    Type='String',
                    Overwrite=True,
                    Description=description
                )
                logger.info(f"Updated: {parameter_name} = {value}")
                return {
                    'status': 'updated',
                    'message': f'Updated from {existing_value} to {value}'
                }
            else:
                logger.info(f"No change: {parameter_name}")
                return {
                    'status': 'unchanged',
                    'message': 'Value already correct'
                }

        except ssm_client.exceptions.ParameterNotFound:
            # Create new parameter
            ssm_client.put_parameter(
                Name=parameter_name,
                Value=value,
                Type='String',
                Description=description
            )
            logger.info(f"Created: {parameter_name} = {value}")
            return {
                'status': 'created',
                'message': f'Created with value {value}'
            }

    except Exception as e:
        error_msg = f"Error managing parameter {parameter_name}: {e}"
        logger.error(error_msg)
        return {
            'status': 'error',
            'message': error_msg
        }


def populate_parameters(
    service_name: str,
    stage: str,
    env_vars: Dict[str, str]
) -> Dict:
    """
    Populate Parameter Store with locally defined variables.

    Args:
        service_name: Service name
        stage: Deployment stage
        env_vars: Environment variables from stack

    Returns:
        Dict with results
    """
    # Extract locally defined variables
    locally_defined = extract_locally_defined_vars(env_vars, service_name, stage)

    if not locally_defined:
        logger.info("No locally defined variables found")
        return {
            'totalVariables': 0,
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'errors': 0,
            'variables': {}
        }

    logger.info(f"Found {len(locally_defined)} locally defined variables to populate")

    # Process each variable
    results = {
        'totalVariables': len(locally_defined),
        'created': 0,
        'updated': 0,
        'unchanged': 0,
        'errors': 0,
        'variables': {}
    }

    for var_name, resolved_value in locally_defined.items():
        # Create parameter path: /amplify/{stage}/{service_name}/{var_name}
        parameter_name = f"/amplify/{stage}/{service_name}/{var_name}"

        # Create or update the parameter
        result = create_or_update_parameter(
            parameter_name=parameter_name,
            value=resolved_value,
            description=f"Locally defined variable from {service_name} service"
        )

        # Track results
        results['variables'][var_name] = result
        if result['status'] == 'created':
            results['created'] += 1
        elif result['status'] == 'updated':
            results['updated'] += 1
        elif result['status'] == 'unchanged':
            results['unchanged'] += 1
        elif result['status'] == 'error':
            results['errors'] += 1

    logger.info(
        f"Parameter Store population complete: "
        f"{results['created']} created, {results['updated']} updated, "
        f"{results['unchanged']} unchanged, {results['errors']} errors"
    )

    return results


def handle_cloudformation_request(event: Dict, context) -> Dict:
    """
    Handle CloudFormation custom resource create/update request.

    This Lambda has access to ALL environment variables from serverless.yml.
    We simply check our own os.environ and sync locally-defined vars to Parameter Store!

    Expected event format:
    {
      "RequestType": "Create" | "Update" | "Delete",
      "ResourceProperties": {
        "ServiceName": "amplify-lambda",  # Optional - can use SERVICE_NAME env var
        "Stage": "dev"  # Optional - can use STAGE env var
      }
    }
    """
    request_type = event.get('RequestType')
    properties = event.get('ResourceProperties', {})

    # On delete, do nothing (keep parameters as-is)
    if request_type == 'Delete':
        logger.info("Delete request - no action needed")
        return send_cfn_response(event, context, 'SUCCESS', {
            'Message': 'Delete operation - no action taken'
        })

    # Get service_name and stage from environment or properties
    service_name = properties.get('ServiceName') or os.environ.get('SERVICE_NAME')
    stage = properties.get('Stage') or os.environ.get('STAGE')

    if not service_name:
        error_msg = 'ServiceName not provided (missing from properties and SERVICE_NAME env var)'
        logger.error(error_msg)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })

    if not stage:
        error_msg = 'Stage not provided (missing from properties and STAGE env var)'
        logger.error(error_msg)
        return send_cfn_response(event, context, 'FAILED', {
            'Error': error_msg
        })

    # Use this Lambda's own environment variables!
    # All locally-defined vars are already here with resolved values
    logger.info(f"Using Lambda's own environment variables (total: {len(os.environ)})")
    env_vars = dict(os.environ)

    # Populate parameters
    try:
        results = populate_parameters(service_name, stage, env_vars)

        # Check for errors
        if results['errors'] > 0:
            logger.warning(f"Some parameters failed to populate: {results['errors']}")
            # Still return SUCCESS because partial failure shouldn't block deployment
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

    Handles CloudFormation custom resource requests to populate
    Parameter Store with locally defined variables.
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
