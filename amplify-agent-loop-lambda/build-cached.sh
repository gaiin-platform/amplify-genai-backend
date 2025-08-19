#!/bin/bash
set -e

REGION="us-east-1"
STAGE="${1:-dev}"
PRUNE=false

# Check for --prune flag
if [[ "$2" == "--prune" ]]; then
    PRUNE=true
fi

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
BASE_TAG_FAT="${STAGE}-agent-router-fat"
BASE_TAG_SLIM="${STAGE}-agent-router"
IMAGE_TAG_FAT="${BASE_TAG_FAT}-${TIMESTAMP}"
IMAGE_TAG_SLIM="${BASE_TAG_SLIM}-${TIMESTAMP}"
CACHE_TAG="${STAGE}-agent-router-cache"

# Export the timestamp for serverless.yml to use
echo "export DEPLOY_TIMESTAMP=$TIMESTAMP" > deploy-timestamp.env

echo "Building container image for stage: $STAGE with dependency caching"
echo "Repository: $REPOSITORY_URI"
echo "Image tag fat: $IMAGE_TAG_FAT"
echo "Image tag slim: $IMAGE_TAG_SLIM"
echo "Cache tag: $CACHE_TAG"
echo "Timestamp: $TIMESTAMP"

# Enable Docker BuildKit explicitly
export DOCKER_BUILDKIT=1

# Login to ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REPOSITORY_URI

# Conditionally clean previous builds based on prune flag
if [ "$PRUNE" = true ]; then
    echo "Pruning Docker system..."
    docker system prune -f
else
    echo "Skipping Docker system prune (use --prune flag to enable)"
fi

# Ensure buildx builder
docker buildx create --name lambda-builder --use || docker buildx use lambda-builder
docker buildx inspect --bootstrap

# Check if the cache image exists
if ! aws ecr describe-images --repository-name $(echo $REPOSITORY_URI | cut -d'/' -f2) --image-ids imageTag=$CACHE_TAG --region $REGION &>/dev/null; then
    echo "Cache image doesn't exist, building fresh image..."
    CACHE_FROM_ARG=""
else
    echo "Using existing cache image: $REPOSITORY_URI:$CACHE_TAG"
    CACHE_FROM_ARG="--cache-from=$REPOSITORY_URI:$CACHE_TAG"
fi

# Build explicitly for Lambda (single-arch, with cache)
docker buildx build \
  --file Dockerfile-fat \
  --platform linux/amd64 \
  --push \
  --provenance=false \
  --sbom=false \
  $CACHE_FROM_ARG \
  --cache-to=type=registry,ref=$REPOSITORY_URI:$CACHE_TAG,mode=max \
  -t $REPOSITORY_URI:$IMAGE_TAG_FAT \
  -t $REPOSITORY_URI:$IMAGE_TAG_SLIM .

echo "Images built and pushed:"
echo "- $REPOSITORY_URI:$IMAGE_TAG_FAT"
echo "- $REPOSITORY_URI:$IMAGE_TAG_SLIM"
echo "Cache saved to: $REPOSITORY_URI:$CACHE_TAG"