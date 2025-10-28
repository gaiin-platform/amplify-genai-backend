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

# AGGRESSIVE CLEANUP - Remove unnecessary files to minimize layer size
echo "Starting aggressive cleanup to minimize layer size..."

## 1. Remove boto3/botocore/s3transfer (23MB) - Already in Lambda runtime
#echo "Removing boto3/botocore (already in Lambda runtime)..."
#rm -rf python/boto3 python/boto3-* || true
#rm -rf python/botocore python/botocore-* || true
#rm -rf python/s3transfer python/s3transfer-* || true
#rm -rf python/jmespath python/jmespath-* || true

# 2. Remove LiteLLM proxy (13MB) - Not needed for client usage
echo "Removing LiteLLM proxy components..."
rm -rf python/litellm/proxy || true

# 3. Remove large optional dependencies
echo "Removing large optional dependencies..."
rm -rf python/hf_xet || true                    # 7.9MB - HuggingFace XET
#rm -rf python/tokenizers || true                # 10MB - Advanced tokenization
rm -rf python/huggingface_hub || true           # 2.2MB - Model hub access

# 4. Remove all CLI executables except python3
echo "Removing unnecessary CLI executables..."
#find python/bin -type f ! -name "python3" -delete || true

# 5. Remove all __pycache__ directories and .pyc files
echo "Removing bytecode caches..."
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true

# 6. Remove .dist-info and .egg-info directories
echo "Removing package metadata..."
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# 7. Remove documentation and examples
echo "Removing documentation and examples..."
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "example" -exec rm -rf {} + 2>/dev/null || true

# 8. Remove test directories
echo "Removing test directories..."
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "testing" -exec rm -rf {} + 2>/dev/null || true

# 9. Remove type stubs (.pyi files)
echo "Removing type stubs..."
find python -name "*.pyi" -delete 2>/dev/null || true

# 10. Remove markdown, rst, and txt documentation files
echo "Removing documentation files..."
find python -type f \( -name "*.md" -o -name "*.rst" -o -name "*.txt" \) ! -name "requirements.txt" -delete 2>/dev/null || true

# 11. Remove LICENSE and NOTICE files (keep compliance but save space)
echo "Removing license files..."
find python -type f \( -name "LICENSE*" -o -name "NOTICE*" -o -name "COPYING*" \) -delete 2>/dev/null || true

# 12. Remove benchmark directories
echo "Removing benchmarks..."
find python -type d -name "benchmarks" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "benchmark" -exec rm -rf {} + 2>/dev/null || true

echo "Aggressive cleanup complete"

# Copy Python 3.11 binary and shared libraries from Lambda container (x86_64 architecture)
echo "Copying Python 3.11 binary and shared libraries (x86_64)..."
docker run --rm --platform linux/amd64 \
  -v $(pwd)/python:/output \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/python:3.11 \
  -c "
    # Copy Python binary
    cp /var/lang/bin/python3.11 /output/bin/python3 && chmod +x /output/bin/python3

    # Copy Python shared libraries
    mkdir -p /output/lib
    cp -r /var/lang/lib/libpython3.11.so* /output/lib/ 2>/dev/null || true
    cp -r /var/lang/lib/python3.11/lib-dynload /output/lib/ 2>/dev/null || true

    # Copy other essential shared libraries that Python depends on
    cp /lib64/libz.so.1 /output/lib/ 2>/dev/null || true
    cp /lib64/libexpat.so.1 /output/lib/ 2>/dev/null || true
  "

echo "Python binary and libraries installed successfully"

# Verify Python binary exists
if [ -f "python/bin/python3" ]; then
    echo "✓ Python binary verified at python/bin/python3"
else
    echo "✗ ERROR: Python binary not found!"
    exit 1
fi

# Verify shared library exists
if [ -f "python/lib/libpython3.11.so.1.0" ]; then
    echo "✓ Python shared library verified at python/lib/libpython3.11.so.1.0"
else
    echo "⚠ WARNING: Python shared library not found (may still work)"
fi

# Note: Aggressive cleanup already done above after pip install
echo "Final cleanup and verification..."

# Show layer size
LAYER_SIZE=$(du -sh python | cut -f1)
echo "======================================"
echo "Layer build complete!"
echo "Layer size: $LAYER_SIZE"
echo "======================================"
echo ""
echo "Size breakdown of largest directories:"
du -sh python/* 2>/dev/null | sort -hr | head -20
