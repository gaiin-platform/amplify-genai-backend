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

# Source the timestamp file to get the DEPLOY_TIMESTAMP variable
if [ -f deploy-timestamp.env ]; then
  source deploy-timestamp.env
  echo "Using timestamp: $DEPLOY_TIMESTAMP"
fi

# Deploy with force flag to ensure it uses the latest container
sls deploy --stage $STAGE --region $REGION --force

# Clean up the timestamp file so it's regenerated next time
if [ -f deploy-timestamp.env ]; then
  rm -f deploy-timestamp.env
  echo "Cleaned up timestamp file for next deployment"
fi

echo "Deployment completed!"