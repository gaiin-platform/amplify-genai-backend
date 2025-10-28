#!/bin/bash
set -e

echo "======================================"
echo "Lambda Layer Build Comparison"
echo "======================================"
echo ""
echo "This script will build the layer twice:"
echo "  1. Standard build (with all files)"
echo "  2. Optimized build (with cleanup)"
echo ""
echo "Then show you the size difference."
echo ""
read -p "This will take 4-6 minutes. Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Clean everything
rm -rf python python_standard python_optimized *.txt

echo ""
echo "======================================"
echo "STEP 1: Building STANDARD layer"
echo "======================================"

# Build standard (using original script logic)
mkdir -p python/bin python/lib

docker run --rm --entrypoint pip \
  -v $(pwd):/var/task \
  -w /var/task \
  public.ecr.aws/lambda/python:3.11 \
  install --no-cache-dir --upgrade \
  -r requirements.txt \
  -t ./python \
  --platform manylinux2014_x86_64 \
  --only-binary=:all:

docker run --rm --platform linux/amd64 \
  -v $(pwd)/python:/output \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/python:3.11 \
  -c "
    cp /var/lang/bin/python3.11 /output/bin/python3 && chmod +x /output/bin/python3
    mkdir -p /output/lib
    cp -r /var/lang/lib/libpython3.11.so* /output/lib/ 2>/dev/null || true
    cp -r /var/lang/lib/python3.11/lib-dynload /output/lib/ 2>/dev/null || true
    cp /lib64/libz.so.1 /output/lib/ 2>/dev/null || true
    cp /lib64/libexpat.so.1 /output/lib/ 2>/dev/null || true
  "

# Basic cleanup only (from original script)
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true
find python -name "*.pyd" -delete 2>/dev/null || true
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true

STANDARD_SIZE=$(du -sh python | cut -f1)
STANDARD_SIZE_BYTES=$(du -sb python | cut -f1)

# Save for comparison
mv python python_standard

echo ""
echo "Standard build complete: $STANDARD_SIZE"

echo ""
echo "======================================"
echo "STEP 2: Building OPTIMIZED layer"
echo "======================================"

# Build optimized (fresh build with aggressive cleanup)
mkdir -p python/bin python/lib

docker run --rm --entrypoint pip \
  -v $(pwd):/var/task \
  -w /var/task \
  public.ecr.aws/lambda/python:3.11 \
  install --no-cache-dir --upgrade \
  -r requirements.txt \
  -t ./python \
  --platform manylinux2014_x86_64 \
  --only-binary=:all:

docker run --rm --platform linux/amd64 \
  -v $(pwd)/python:/output \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/python:3.11 \
  -c "
    cp /var/lang/bin/python3.11 /output/bin/python3 && chmod +x /output/bin/python3
    mkdir -p /output/lib
    cp -r /var/lang/lib/libpython3.11.so* /output/lib/ 2>/dev/null || true
    cp -r /var/lang/lib/python3.11/lib-dynload /output/lib/ 2>/dev/null || true
    cp /lib64/libz.so.1 /output/lib/ 2>/dev/null || true
    cp /lib64/libexpat.so.1 /output/lib/ 2>/dev/null || true
  "

# AGGRESSIVE cleanup
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true
find python -name "*.pyd" -delete 2>/dev/null || true
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "example" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "testing" -exec rm -rf {} + 2>/dev/null || true
find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" -o -name "LICENSE*" -o -name "COPYING*" -o -name "AUTHORS*" -o -name "CHANGELOG*" -o -name "HISTORY*" \) -delete 2>/dev/null || true
find python -name "*.pyi" -delete 2>/dev/null || true
find python -type d -name "benchmark*" -exec rm -rf {} + 2>/dev/null || true
rm -rf python/pip* python/setuptools* python/wheel* python/_distutils_hack 2>/dev/null || true
find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) -delete 2>/dev/null || true
find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" -o -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) -delete 2>/dev/null || true
find python -name "*.so*" -type f -exec strip --strip-debug {} \; 2>/dev/null || true
rm -rf python/mypy* python/typing_inspect 2>/dev/null || true

OPTIMIZED_SIZE=$(du -sh python | cut -f1)
OPTIMIZED_SIZE_BYTES=$(du -sb python | cut -f1)

# Save for comparison
mv python python_optimized

echo ""
echo "Optimized build complete: $OPTIMIZED_SIZE"

# Calculate savings
SAVED_BYTES=$((STANDARD_SIZE_BYTES - OPTIMIZED_SIZE_BYTES))
SAVED_MB=$((SAVED_BYTES / 1024 / 1024))
PERCENT_SAVED=$((SAVED_BYTES * 100 / STANDARD_SIZE_BYTES))

echo ""
echo "======================================"
echo "COMPARISON RESULTS"
echo "======================================"
echo ""
printf "%-25s %15s\n" "Standard build:" "$STANDARD_SIZE"
printf "%-25s %15s\n" "Optimized build:" "$OPTIMIZED_SIZE"
printf "%-25s %15s\n" "Space saved:" "${SAVED_MB}MB"
printf "%-25s %15s\n" "Percentage saved:" "${PERCENT_SAVED}%"
echo ""

echo "======================================"
echo "SIZE BREAKDOWN"
echo "======================================"
echo ""
echo "Standard build top directories:"
du -h python_standard/* 2>/dev/null | sort -rh | head -15
echo ""
echo "Optimized build top directories:"
du -h python_optimized/* 2>/dev/null | sort -rh | head -15
echo ""

echo "======================================"
echo "FILE COUNT COMPARISON"
echo "======================================"
echo ""
STANDARD_FILES=$(find python_standard -type f | wc -l)
OPTIMIZED_FILES=$(find python_optimized -type f | wc -l)
FILES_REMOVED=$((STANDARD_FILES - OPTIMIZED_FILES))
printf "%-25s %15s\n" "Standard file count:" "$STANDARD_FILES"
printf "%-25s %15s\n" "Optimized file count:" "$OPTIMIZED_FILES"
printf "%-25s %15s\n" "Files removed:" "$FILES_REMOVED"
echo ""

echo "======================================"
echo "RECOMMENDATIONS"
echo "======================================"
echo ""
if [ $PERCENT_SAVED -ge 30 ]; then
    echo "✅ Excellent! Saved ${PERCENT_SAVED}% (${SAVED_MB}MB)"
    echo "   The optimized build is significantly smaller."
elif [ $PERCENT_SAVED -ge 20 ]; then
    echo "✅ Good! Saved ${PERCENT_SAVED}% (${SAVED_MB}MB)"
    echo "   Decent size reduction achieved."
else
    echo "⚠️  Moderate savings: ${PERCENT_SAVED}% (${SAVED_MB}MB)"
    echo "   Consider additional optimizations if needed."
fi
echo ""

echo "To use the optimized build:"
echo "  mv python_optimized python"
echo "  serverless deploy --stage dev"
echo ""

echo "To use the standard build:"
echo "  mv python_standard python"
echo "  serverless deploy --stage dev"
echo ""

echo "To keep both for testing:"
echo "  cp -r python_optimized python  # Use optimized"
echo "  # Test deployment and functionality"
echo "  # If issues arise, fall back to standard"
echo ""

echo "Comparison complete!"
