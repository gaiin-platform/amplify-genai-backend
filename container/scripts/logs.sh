#!/bin/bash
# Tail CloudWatch logs for ECS service

set -e

REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="${1:-dev}"
FOLLOW="${2:-false}"

# Read deployment name from var file
VAR_FILE="../../var/${ENVIRONMENT}-var.yml"
if [ -f "$VAR_FILE" ]; then
    DEPLOYMENT_NAME=$(grep "DEP_NAME:" "$VAR_FILE" | awk '{print $2}' | tr -d '"')
else
    echo "Error: var file not found at ${VAR_FILE}"
    exit 1
fi

LOG_GROUP="/ecs/${DEPLOYMENT_NAME}-amplify-chat-fargate-${ENVIRONMENT}"

echo "Fetching logs from: ${LOG_GROUP}"
echo ""

if [ "$FOLLOW" = "true" ] || [ "$FOLLOW" = "follow" ]; then
    echo "Following logs (Ctrl+C to stop)..."
    aws logs tail "${LOG_GROUP}" --follow --region "${REGION}"
else
    echo "Showing last 50 log entries..."
    aws logs tail "${LOG_GROUP}" --since 10m --region "${REGION}"
    echo ""
    echo "To follow logs in real-time, run:"
    echo "  ./logs.sh ${ENVIRONMENT} follow"
fi
