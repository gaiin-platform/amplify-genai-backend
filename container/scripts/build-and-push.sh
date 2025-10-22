#!/bin/bash
# Build and Push Docker Image to ECR

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

# Check if required commands exist
command -v aws >/dev/null 2>&1 || { echo -e "${RED}AWS CLI is required but not installed.${NC}" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo -e "${RED}Docker is required but not installed.${NC}" >&2; exit 1; }

echo -e "${GREEN}=== Amplify Chat Fargate - Build and Push ===${NC}"
echo "Environment: ${ENVIRONMENT}"
echo "Image Tag: ${IMAGE_TAG}"
echo "Region: ${REGION}"
echo ""

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ -z "$ACCOUNT_ID" ]; then
    echo -e "${RED}Failed to get AWS account ID. Check your AWS credentials.${NC}"
    exit 1
fi
echo "Account ID: ${ACCOUNT_ID}"

# Read deployment name from var file if it exists
VAR_FILE="../var/${ENVIRONMENT}-var.yml"
if [ -f "$VAR_FILE" ]; then
    DEPLOYMENT_NAME=$(grep "DEP_NAME:" "$VAR_FILE" | awk '{print $2}' | tr -d '"')
    echo "Deployment Name: ${DEPLOYMENT_NAME}"
else
    echo -e "${YELLOW}Warning: var file not found at ${VAR_FILE}${NC}"
    echo "Using default deployment name"
    DEPLOYMENT_NAME="default"
fi

REPO_NAME="${DEPLOYMENT_NAME}-amplify-chat-fargate-${ENVIRONMENT}"
ECR_URL="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo ""
echo -e "${YELLOW}Step 1: Checking ECR repository...${NC}"

# Check if ECR repository exists, create if it doesn't
if ! aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "Creating ECR repository: ${REPO_NAME}"
    aws ecr create-repository \
        --repository-name "$REPO_NAME" \
        --region "$REGION" \
        --image-scanning-configuration scanOnPush=true \
        --encryption-configuration encryptionType=AES256
    echo -e "${GREEN}✓ Repository created${NC}"
else
    echo -e "${GREEN}✓ Repository exists${NC}"
fi

echo ""
echo -e "${YELLOW}Step 2: Logging into ECR...${NC}"

# Login to ECR
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
echo -e "${GREEN}✓ Logged in to ECR${NC}"

echo ""
echo -e "${YELLOW}Step 3: Building Docker image...${NC}"

# Build from project root
cd "$(dirname "$0")/../.."

# Build the image
docker build \
    -f container/Dockerfile \
    -t "${REPO_NAME}:${IMAGE_TAG}" \
    -t "${REPO_NAME}:latest" \
    --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --build-arg VERSION="${IMAGE_TAG}" \
    .

echo -e "${GREEN}✓ Image built successfully${NC}"

echo ""
echo -e "${YELLOW}Step 4: Tagging images...${NC}"

# Tag for ECR
docker tag "${REPO_NAME}:${IMAGE_TAG}" "${ECR_URL}:${IMAGE_TAG}"
docker tag "${REPO_NAME}:latest" "${ECR_URL}:latest"

echo -e "${GREEN}✓ Images tagged${NC}"

echo ""
echo -e "${YELLOW}Step 5: Pushing to ECR...${NC}"

# Push both tags
docker push "${ECR_URL}:${IMAGE_TAG}"
docker push "${ECR_URL}:latest"

echo -e "${GREEN}✓ Images pushed successfully${NC}"

echo ""
echo -e "${GREEN}=== Build Complete ===${NC}"
echo ""
echo "Image URI: ${ECR_URL}:${IMAGE_TAG}"
echo ""
echo "To deploy with Terraform:"
echo "  cd container/terraform"
echo "  terraform apply -var=\"container_image=${ECR_URL}:${IMAGE_TAG}\""
echo ""
echo "To update ECS service with new image:"
echo "  cd container/scripts"
echo "  ./deploy.sh ${ENVIRONMENT} ${IMAGE_TAG}"
