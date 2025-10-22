#!/bin/bash
# Deploy new task definition to ECS service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="${1:-dev}"
IMAGE_TAG="${2:-latest}"

echo -e "${GREEN}=== Amplify Chat Fargate - Deploy ===${NC}"
echo "Environment: ${ENVIRONMENT}"
echo "Image Tag: ${IMAGE_TAG}"
echo "Region: ${REGION}"
echo ""

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Read deployment name from var file
VAR_FILE="../../var/${ENVIRONMENT}-var.yml"
if [ -f "$VAR_FILE" ]; then
    DEPLOYMENT_NAME=$(grep "DEP_NAME:" "$VAR_FILE" | awk '{print $2}' | tr -d '"')
else
    echo -e "${RED}Error: var file not found at ${VAR_FILE}${NC}"
    exit 1
fi

CLUSTER_NAME="${DEPLOYMENT_NAME}-amplify-chat-fargate-${ENVIRONMENT}"
SERVICE_NAME="${DEPLOYMENT_NAME}-amplify-chat-fargate-${ENVIRONMENT}"

echo "Cluster: ${CLUSTER_NAME}"
echo "Service: ${SERVICE_NAME}"
echo ""

echo -e "${YELLOW}Forcing new deployment...${NC}"

# Force new deployment
aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${SERVICE_NAME}" \
    --force-new-deployment \
    --region "${REGION}" \
    >/dev/null

echo -e "${GREEN}✓ Deployment initiated${NC}"
echo ""
echo -e "${YELLOW}Waiting for service to stabilize...${NC}"

# Wait for service to stabilize
aws ecs wait services-stable \
    --cluster "${CLUSTER_NAME}" \
    --services "${SERVICE_NAME}" \
    --region "${REGION}"

echo -e "${GREEN}✓ Service is stable${NC}"
echo ""

# Get service status
echo -e "${YELLOW}Service Status:${NC}"
aws ecs describe-services \
    --cluster "${CLUSTER_NAME}" \
    --services "${SERVICE_NAME}" \
    --region "${REGION}" \
    --query 'services[0].{
        Status: status,
        DesiredCount: desiredCount,
        RunningCount: runningCount,
        PendingCount: pendingCount,
        TaskDefinition: taskDefinition
    }' \
    --output table

echo ""
echo -e "${GREEN}=== Deployment Complete ===${NC}"
