#!/bin/bash
# Test the container locally with Docker

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ENVIRONMENT="${1:-dev}"
PORT="${2:-8080}"

echo -e "${GREEN}=== Testing Amplify Chat Container Locally ===${NC}"
echo "Environment: ${ENVIRONMENT}"
echo "Port: ${PORT}"
echo ""

# Check if .env file exists
if [ ! -f "container/.env.${ENVIRONMENT}" ]; then
    echo -e "${YELLOW}Warning: container/.env.${ENVIRONMENT} not found${NC}"
    echo "Create this file with your environment variables"
    echo ""
fi

# Build the image
echo -e "${YELLOW}Building Docker image...${NC}"
docker build -f container/Dockerfile -t amplify-chat:local .
echo -e "${GREEN}✓ Image built${NC}"
echo ""

# Run the container
echo -e "${YELLOW}Starting container...${NC}"
echo "Container will be available at: http://localhost:${PORT}"
echo "Press Ctrl+C to stop"
echo ""

# Run with env file if it exists
if [ -f "container/.env.${ENVIRONMENT}" ]; then
    docker run --rm -it \
        -p "${PORT}:8080" \
        --env-file "container/.env.${ENVIRONMENT}" \
        --name amplify-chat-local \
        amplify-chat:local
else
    echo "Running without environment file..."
    docker run --rm -it \
        -p "${PORT}:8080" \
        -e PORT=8080 \
        -e NODE_ENV=development \
        --name amplify-chat-local \
        amplify-chat:local
fi
