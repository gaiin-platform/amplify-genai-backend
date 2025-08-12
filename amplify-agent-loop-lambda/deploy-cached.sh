#!/bin/bash
set -e

# Set the stage from command line or default to dev
STAGE="${1:-dev}"
export STAGE=$STAGE

# Set region from command line or default to the one in AWS config
REGION="${2:-$(aws configure get region)}"
export REGION=$REGION

echo "Deploying to stage: $STAGE in region: $REGION"

# Run the cached build script instead of regular build
./build-cached.sh $STAGE

# Deploy with serverless using appropriate config file based on environment
if [ "$STAGE" = "prod" ]; then
    echo "Using serverless_internal.yml for production environment"
    sls deploy --config serverless_internal.yml --stage $STAGE --region $REGION
else
    echo "Using serverless.yml for development environment"
    sls deploy --stage $STAGE --region $REGION
fi

echo "Deployment completed!"