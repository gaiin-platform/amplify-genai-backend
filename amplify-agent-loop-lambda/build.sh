#!/bin/bash
set -e

REGION="us-east-1"
STAGE="${1:-dev}"
DEV_REPOSITORY_URI="654654422653.dkr.ecr.us-east-1.amazonaws.com/dev-amplifygenai-repo"
PROD_REPOSITORY_URI="514391678313.dkr.ecr.us-east-1.amazonaws.com/prod-amplifygenai-repo"

# Set repository URI based on stage
if [ "$STAGE" = "prod" ]; then
    REPOSITORY_URI="$PROD_REPOSITORY_URI"
else
    REPOSITORY_URI="$DEV_REPOSITORY_URI"
fi

# Add timestamp to ensure unique tag for each build
TIMESTAMP=$(date +%s)
BASE_IMAGE_TAG="${STAGE}-agent-router"
IMAGE_TAG="${BASE_IMAGE_TAG}-${TIMESTAMP}"

# Export the timestamp for serverless.yml to use
echo "export DEPLOY_TIMESTAMP=$TIMESTAMP" > deploy-timestamp.env

echo "Building container image for stage: $STAGE"
echo "Repository: $REPOSITORY_URI"
echo "Image tag: $IMAGE_TAG"
echo "Timestamp: $TIMESTAMP"

# Enable Docker BuildKit explicitly
export DOCKER_BUILDKIT=1

# Login to ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REPOSITORY_URI

# Clean previous builds
docker system prune -f

# Ensure buildx builder
docker buildx create --name lambda-builder --use || docker buildx use lambda-builder
docker buildx inspect --bootstrap

# Build explicitly for Lambda (single-arch, clean manifest)
docker buildx build \
  --platform linux/amd64 \
  --push \
  --provenance=false \
  --sbom=false \
  --no-cache \
  -t $REPOSITORY_URI:$IMAGE_TAG .

echo "Image built and pushed: $REPOSITORY_URI:$IMAGE_TAG"
