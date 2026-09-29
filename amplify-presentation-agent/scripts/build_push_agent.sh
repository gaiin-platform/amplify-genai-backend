#!/usr/bin/env bash
# Build the presentation agent container (linux/arm64, required by AgentCore Runtime),
# push it to ECR, and print the image URI to set as PRESENTATION_AGENT_IMAGE_URI in
# var/<stage>-var.yml before deploying the amplify-presentation-agent service.
#
# Usage: scripts/build_push_agent.sh <stage> [aws-region]
set -euo pipefail

STAGE="${1:?usage: build_push_agent.sh <stage> [aws-region]}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
REPO="amplify-presentation-agent-${STAGE}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TAG="$(git -C "$HERE" rev-parse --short HEAD)$(git -C "$HERE" diff --quiet -- agent || echo "-dirty")"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --region "$REGION" --repository-name "$REPO" \
       --image-scanning-configuration scanOnPush=true >/dev/null

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 -t "$IMAGE" --push "$HERE/agent"

echo
echo "Pushed ${IMAGE}"
echo "Set in var/${STAGE}-var.yml:"
echo "  PRESENTATION_AGENT_IMAGE_URI: \"${IMAGE}\""
