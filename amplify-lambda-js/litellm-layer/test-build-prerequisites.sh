#!/usr/bin/env bash
set -e

echo "========================================"
echo "Testing Build Prerequisites"
echo "========================================"

# Check for required commands
MISSING=()

echo "Checking for required commands..."

if ! command -v curl &> /dev/null; then
    MISSING+=("curl")
else
    echo "✓ curl found: $(command -v curl)"
fi

if ! command -v tar &> /dev/null; then
    MISSING+=("tar")
else
    echo "✓ tar found: $(command -v tar)"
fi

if ! command -v zstd &> /dev/null; then
    MISSING+=("zstd")
else
    echo "✓ zstd found: $(command -v zstd)"
fi

if ! command -v zip &> /dev/null; then
    MISSING+=("zip")
else
    echo "✓ zip found: $(command -v zip)"
fi

if [ ${#MISSING[@]} -ne 0 ]; then
    echo ""
    echo "❌ Missing required commands: ${MISSING[*]}"
    echo ""
    echo "Install missing commands:"
    echo "  macOS: brew install ${MISSING[*]}"
    echo "  Ubuntu: apt-get install ${MISSING[*]}"
    exit 1
fi

echo ""
echo "✅ All prerequisites satisfied!"
echo ""

# Test tar with zstd support
echo "Testing tar with zstd support..."
if tar --version | grep -q "zstd"; then
    echo "✓ tar has zstd support"
elif command -v zstd &> /dev/null; then
    echo "✓ zstd available as separate command (tar will use it)"
else
    echo "⚠ Warning: tar may not support zstd compression"
fi

echo ""
echo "========================================"
echo "Build environment ready!"
echo "========================================"
echo ""
echo "To build the layer:"
echo "  cd scripts"
echo "  ./build-python-litellm-layer.sh"
echo ""
echo "To build for x86_64:"
echo "  ARCH=x86_64 ./build-python-litellm-layer.sh"
