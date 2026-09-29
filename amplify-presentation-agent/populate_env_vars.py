"""CloudFormation custom resource handler for Parameter Store population."""
import logging

from pycommon.deployment.parameter_store_sync import cfn_handler as lambda_handler  # noqa: F401

logger = logging.getLogger()
logger.setLevel(logging.INFO)

