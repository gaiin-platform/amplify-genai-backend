#!/usr/bin/env bash
set -e

echo "======================================"
echo "Python LiteLLM Layer Builder"
echo "======================================"
echo ""

# Get the directory where this script is located (litellm-layer/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "${SCRIPT_DIR}"

# Check if we should run prerequisites test first
if [ "$1" == "--skip-check" ]; then
    echo "Skipping prerequisites check..."
else
    echo "Checking prerequisites..."
    ./test-build-prerequisites.sh
    echo ""
fi

# Build the layer
echo "Starting layer build..."
echo ""
./build-python-litellm-layer.sh

echo ""
echo "======================================"
echo "Build Complete!"
echo "======================================"
echo ""
echo "Layer artifact:"
ls -lh layer-build-*/python-litellm-*.zip
echo ""
echo "Next steps:"
echo "  1. Test: cd ../amplify-lambda-js && serverless deploy --stage dev"
echo "  2. Monitor CloudWatch logs"
echo "  3. If successful, deploy to staging"
echo ""
echo "Documentation:"
echo "  - Quick Start: QUICK_START_PBS.md"
echo "  - Full Guide: README-PBS.md"
echo "  - Implementation: IMPLEMENTATION_SUMMARY.md"
