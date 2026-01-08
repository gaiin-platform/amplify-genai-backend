#!/bin/bash
set -e

REGION="us-east-1"
STAGE="${1:-dev}"
PRUNE=false
NO_CACHE=false

# Check for --prune and --no-cache flags
if [[ "$2" == "--prune" ]]; then
    PRUNE=true
elif [[ "$2" == "--no-cache" ]]; then
    NO_CACHE=true
fi

if [[ "$3" == "--no-cache" ]]; then
    NO_CACHE=true
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

# Check if the cache image exists and --no-cache flag
if [ "$NO_CACHE" = true ]; then
    echo "NO_CACHE flag enabled - skipping cache completely and busting Docker layer cache"
    CACHE_FROM_ARG=""
    CACHE_TO_ARG=""
    CACHE_BUST_ARG="--build-arg CACHE_BUST=$(date +%s)"
elif ! aws ecr describe-images --repository-name $(echo $REPOSITORY_URI | cut -d'/' -f2) --image-ids imageTag=$CACHE_TAG --region $REGION &>/dev/null; then
    echo "Cache image doesn't exist, building fresh image..."
    CACHE_FROM_ARG=""
    CACHE_TO_ARG="--cache-to=type=registry,ref=$REPOSITORY_URI:$CACHE_TAG,mode=max"
    CACHE_BUST_ARG=""
else
    echo "Using existing cache image: $REPOSITORY_URI:$CACHE_TAG"
    CACHE_FROM_ARG="--cache-from=$REPOSITORY_URI:$CACHE_TAG"
    CACHE_TO_ARG="--cache-to=type=registry,ref=$REPOSITORY_URI:$CACHE_TAG,mode=max"
    CACHE_BUST_ARG=""
fi

# Build explicitly for Lambda (single-arch, with cache)
docker buildx build \
  --file Dockerfile-fat \
  --platform linux/amd64 \
  --push \
  --provenance=false \
  --sbom=false \
  $CACHE_FROM_ARG \
  $CACHE_TO_ARG \
  $CACHE_BUST_ARG \
  -t $REPOSITORY_URI:$IMAGE_TAG_FAT \
  -t $REPOSITORY_URI:$IMAGE_TAG_SLIM .

echo "Images built and pushed:"
echo "- $REPOSITORY_URI:$IMAGE_TAG_FAT"
echo "- $REPOSITORY_URI:$IMAGE_TAG_SLIM"
echo "Cache saved to: $REPOSITORY_URI:$CACHE_TAG"