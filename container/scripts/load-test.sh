#!/bin/bash
# Load test script to determine actual concurrent capacity

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ENDPOINT="${1}"
TOKEN="${2}"
CONCURRENT="${3:-10}"
DURATION="${4:-60}"

if [ -z "$ENDPOINT" ] || [ -z "$TOKEN" ]; then
    echo "Usage: ./load-test.sh <endpoint> <token> [concurrent=10] [duration=60]"
    echo "Example: ./load-test.sh http://my-alb.aws.com/chat \$AUTH_TOKEN 20 60"
    exit 1
fi

echo -e "${GREEN}=== Amplify Chat Load Test ===${NC}"
echo "Endpoint: ${ENDPOINT}"
echo "Concurrent: ${CONCURRENT}"
echo "Duration: ${DURATION}s"
echo ""

# Check if hey is installed
if ! command -v hey &> /dev/null; then
    echo -e "${RED}Error: 'hey' load testing tool not installed${NC}"
    echo "Install with: brew install hey  (macOS)"
    echo "          or: apt-get install hey  (Linux)"
    exit 1
fi

# Create test payload
cat > /tmp/chat-payload.json <<EOF
{
  "messages": [
    {"role": "user", "content": "Hello, this is a load test. Please respond briefly."}
  ],
  "options": {
    "model": {"id": "gpt-4o-mini"},
    "temperature": 0.7
  }
}
EOF

echo -e "${YELLOW}Starting load test...${NC}"
echo "This will make ${CONCURRENT} concurrent requests for ${DURATION} seconds"
echo ""

# Run load test
hey -z ${DURATION}s \
    -c ${CONCURRENT} \
    -m POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -D /tmp/chat-payload.json \
    "${ENDPOINT}"

echo ""
echo -e "${GREEN}=== Test Complete ===${NC}"
echo ""
echo "Interpretation:"
echo "- Response time p50: Target <2s"
echo "- Response time p95: Target <5s"
echo "- Success rate: Target >99%"
echo "- Errors: Should be 0"
echo ""
echo "If you see errors or high latency, scale up:"
echo "  cd container/terraform"
echo "  Edit terraform.tfvars: increase desired_count"
echo "  terraform apply"
