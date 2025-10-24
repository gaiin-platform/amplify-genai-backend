#!/bin/bash
set -e

echo "======================================"
echo "Building Python LiteLLM Lambda Layer"
echo "======================================"

# Clean previous build
rm -rf python
mkdir -p python/bin
mkdir -p python/lib

# Install Python dependencies using Docker for Lambda compatibility
echo "Installing Python dependencies with Docker..."
docker run --rm --entrypoint pip \
  -v $(pwd):/var/task \
  -w /var/task \
  public.ecr.aws/lambda/python:3.11 \
  install --no-cache-dir --upgrade \
  -r requirements.txt \
  -t ./python \
  --platform manylinux2014_x86_64 \
  --only-binary=:all:

echo "Python packages installed successfully"

# Copy Python 3.11 binary from Lambda container (x86_64 architecture)
echo "Copying Python 3.11 binary (x86_64)..."
docker run --rm --platform linux/amd64 \
  -v $(pwd)/python/bin:/output \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/python:3.11 \
  -c "cp /var/lang/bin/python3.11 /output/python3 && chmod +x /output/python3"

echo "Python binary installed successfully"

# Verify Python binary exists
if [ -f "python/bin/python3" ]; then
    echo "✓ Python binary verified at python/bin/python3"
else
    echo "✗ ERROR: Python binary not found!"
    exit 1
fi

# Clean up unnecessary files to reduce layer size
echo "Cleaning up unnecessary files..."
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true
find python -name "*.pyd" -delete 2>/dev/null || true

# Remove documentation and tests
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true

echo "Cleanup complete"

# Show layer size
LAYER_SIZE=$(du -sh python | cut -f1)
echo "======================================"
echo "Layer build complete!"
echo "Layer size: $LAYER_SIZE"
echo "======================================"
echo ""
echo "Layer contents:"
ls -lh python/ | head -20
