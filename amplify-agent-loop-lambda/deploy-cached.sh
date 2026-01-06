#!/bin/bash
set -e

# Parse command line arguments
STAGE="${1:-dev}"
REGION="${2:-$(aws configure get region)}"
PRUNE=false
NO_CACHE=false

# Check for --prune and --no-cache flags
if [[ "$1" == "--prune" ]]; then
    PRUNE=true
    STAGE="${2:-dev}"
    REGION="${3:-$(aws configure get region)}"
elif [[ "$1" == "--no-cache" ]]; then
    NO_CACHE=true
    STAGE="${2:-dev}"
    REGION="${3:-$(aws configure get region)}"
elif [[ "$2" == "--prune" ]]; then
    PRUNE=true
    REGION="${3:-$(aws configure get region)}"
elif [[ "$2" == "--no-cache" ]]; then
    NO_CACHE=true
    REGION="${3:-$(aws configure get region)}"
elif [[ "$3" == "--prune" ]]; then
    PRUNE=true
elif [[ "$3" == "--no-cache" ]]; then
    NO_CACHE=true
fi

export STAGE=$STAGE
export REGION=$REGION

echo "Deploying to stage: $STAGE in region: $REGION"
if [ "$PRUNE" = true ]; then
    echo "Prune option enabled - will clean Docker system before build"
fi
if [ "$NO_CACHE" = true ]; then
    echo "NO_CACHE option enabled - will skip all Docker caching"
fi

# Run the cached build script with options
BUILD_OPTS="$STAGE"
if [ "$PRUNE" = true ]; then
    BUILD_OPTS="$BUILD_OPTS --prune"
fi
if [ "$NO_CACHE" = true ]; then
    BUILD_OPTS="$BUILD_OPTS --no-cache"
fi

./build-cached.sh $BUILD_OPTS

# Source the timestamp file to get the DEPLOY_TIMESTAMP variable
if [ -f deploy-timestamp.env ]; then
  source deploy-timestamp.env
  echo "Using timestamp: $DEPLOY_TIMESTAMP"
fi

# Deploy with serverless
sls deploy --stage $STAGE --region $REGION --force

# Clean up the timestamp file so it's regenerated next time
if [ -f deploy-timestamp.env ]; then
  rm -f deploy-timestamp.env
  echo "Cleaned up timestamp file for next deployment"
fi

echo "Deployment completed!"