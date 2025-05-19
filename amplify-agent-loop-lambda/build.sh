#!/bin/bash
set -e

REGION="us-east-1"
STAGE="${1:-dev}"
REPOSITORY_URI="654654422653.dkr.ecr.us-east-1.amazonaws.com/dev-amplifygenai-repo"
IMAGE_TAG="${STAGE}-agent-router"

echo "Building container image for stage: $STAGE"
echo "Repository: $REPOSITORY_URI"
echo "Image tag: $IMAGE_TAG"

# Ensure BuildKit is enabled explicitly
export DOCKER_BUILDKIT=1

# Login to ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REPOSITORY_URI

# Clean previous builds
docker system prune -f

# Explicitly create a new buildx builder for single-arch builds (do this once)
docker buildx create --name lambda-builder --use || docker buildx use lambda-builder
docker buildx inspect --bootstrap

# Build and push explicitly targeting amd64 (Lambda's preferred architecture)
docker buildx build --platform linux/amd64 \
  --push \
  -t $REPOSITORY_URI:$IMAGE_TAG .

echo "Image built and pushed: $REPOSITORY_URI:$IMAGE_TAG"
