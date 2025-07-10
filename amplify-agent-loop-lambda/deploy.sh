#!/bin/bash
set -e

# Set the stage from command line or default to dev
STAGE="${1:-dev}"
export STAGE=$STAGE

# Set region from command line or default to the one in AWS config
REGION="${2:-$(aws configure get region)}"
export REGION=$REGION

echo "Deploying to stage: $STAGE in region: $REGION"

# Run the build script
#./build.sh $STAGE
./build-fat.sh $STAGE

sls deploy --stage $STAGE --region $REGION

echo "Deployment completed!"