#!/bin/bash
set -e

echo "======================================"
echo "Building OPTIMIZED Python LiteLLM Lambda Layer"
echo "======================================"

# Clean previous build
rm -rf python python_analysis.txt
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

# Get initial size
INITIAL_SIZE=$(du -sh python | cut -f1)
echo "Initial size after pip install: $INITIAL_SIZE"

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

echo ""
echo "======================================"
echo "AGGRESSIVE OPTIMIZATION PHASE"
echo "======================================"

# Track what we're removing
echo "Analyzing what can be removed..." > python_analysis.txt

# 1. Remove __pycache__ directories
echo "Removing __pycache__ directories..."
PYCACHE_SIZE=$(du -sh $(find python -type d -name "__pycache__" 2>/dev/null) 2>/dev/null | awk '{s+=$1}END{print s}' || echo "0")
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "  __pycache__: Saved ~${PYCACHE_SIZE}MB" | tee -a python_analysis.txt

# 2. Remove .pyc, .pyo, .pyd files
echo "Removing compiled Python files..."
PYC_COUNT=$(find python -name "*.pyc" -o -name "*.pyo" -o -name "*.pyd" 2>/dev/null | wc -l)
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true
find python -name "*.pyd" -delete 2>/dev/null || true
echo "  Compiled files (.pyc/.pyo/.pyd): Removed ${PYC_COUNT} files" | tee -a python_analysis.txt

# 3. Remove dist-info and egg-info
echo "Removing package metadata..."
DISTINFO_SIZE=$(du -ch $(find python -type d -name "*.dist-info" -o -name "*.egg-info" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
echo "  Package metadata (.dist-info/.egg-info): Saved ~${DISTINFO_SIZE}" | tee -a python_analysis.txt

# 4. Remove documentation
echo "Removing documentation..."
DOC_SIZE=$(du -ch $(find python -type d -name "doc" -o -name "docs" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
echo "  Documentation (doc/docs): Saved ~${DOC_SIZE}" | tee -a python_analysis.txt

# 5. Remove examples
echo "Removing examples..."
EXAMPLES_SIZE=$(du -ch $(find python -type d -name "examples" -o -name "example" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "example" -exec rm -rf {} + 2>/dev/null || true
echo "  Examples: Saved ~${EXAMPLES_SIZE}" | tee -a python_analysis.txt

# 6. Remove tests
echo "Removing tests..."
TESTS_SIZE=$(du -ch $(find python -type d -name "tests" -o -name "test" -o -name "testing" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "testing" -exec rm -rf {} + 2>/dev/null || true
echo "  Tests: Saved ~${TESTS_SIZE}" | tee -a python_analysis.txt

# 7. Remove .md, .txt, .rst files (documentation)
echo "Removing README, LICENSE, and documentation files..."
MD_COUNT=$(find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" -o -name "LICENSE*" -o -name "COPYING*" -o -name "AUTHORS*" -o -name "CHANGELOG*" -o -name "HISTORY*" \) 2>/dev/null | wc -l)
find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" -o -name "LICENSE*" -o -name "COPYING*" -o -name "AUTHORS*" -o -name "CHANGELOG*" -o -name "HISTORY*" \) -delete 2>/dev/null || true
echo "  Documentation files (.md/.txt/.rst/LICENSE): Removed ${MD_COUNT} files" | tee -a python_analysis.txt

# 8. Remove .pyi stub files
echo "Removing type stub files..."
PYI_COUNT=$(find python -name "*.pyi" 2>/dev/null | wc -l)
find python -name "*.pyi" -delete 2>/dev/null || true
echo "  Type stubs (.pyi): Removed ${PYI_COUNT} files" | tee -a python_analysis.txt

# 9. Remove benchmark directories
echo "Removing benchmarks..."
BENCH_SIZE=$(du -ch $(find python -type d -name "benchmark*" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
find python -type d -name "benchmark*" -exec rm -rf {} + 2>/dev/null || true
echo "  Benchmarks: Saved ~${BENCH_SIZE}" | tee -a python_analysis.txt

# 10. Remove .so debug symbols (strip binaries)
echo "Stripping debug symbols from .so files..."
SO_BEFORE=$(find python -name "*.so*" -type f -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
find python -name "*.so*" -type f -exec strip --strip-debug {} \; 2>/dev/null || true
SO_AFTER=$(find python -name "*.so*" -type f -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
echo "  Stripped .so files: ${SO_BEFORE} -> ${SO_AFTER}" | tee -a python_analysis.txt

# 11. Remove specific heavy/unnecessary packages
echo "Removing unnecessary heavy packages..."

# Remove pip, setuptools, wheel (not needed at runtime)
rm -rf python/pip* python/setuptools* python/wheel* python/_distutils_hack 2>/dev/null || true
echo "  Removed pip/setuptools/wheel" | tee -a python_analysis.txt

# Remove pkg_resources tests
rm -rf python/pkg_resources/tests 2>/dev/null || true

# Remove typing_extensions tests (if any)
rm -rf python/typing_extensions/tests 2>/dev/null || true

# 12. Remove .c and .h files (source code not needed)
echo "Removing C source files..."
C_COUNT=$(find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) 2>/dev/null | wc -l)
find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) -delete 2>/dev/null || true
echo "  C/C++ source files: Removed ${C_COUNT} files" | tee -a python_analysis.txt

# 13. Remove .gitignore, .gitattributes, .coveragerc, etc.
echo "Removing version control and config files..."
VCS_COUNT=$(find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" -o -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) 2>/dev/null | wc -l)
find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" -o -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) -delete 2>/dev/null || true
echo "  VCS and config files: Removed ${VCS_COUNT} files" | tee -a python_analysis.txt

# 14. Remove type checking related packages if they exist
rm -rf python/mypy* python/typing_inspect 2>/dev/null || true

# 15. Find and report largest remaining directories
echo ""
echo "Largest remaining directories (top 20):" | tee -a python_analysis.txt
du -h python/* 2>/dev/null | sort -rh | head -20 | tee -a python_analysis.txt

echo ""
echo "Cleanup complete"

# Show final layer size
FINAL_SIZE=$(du -sh python | cut -f1)
echo ""
echo "======================================"
echo "Layer build complete!"
echo "======================================"
echo "Initial size:  $INITIAL_SIZE"
echo "Final size:    $FINAL_SIZE"
echo "======================================"
echo ""
echo "Top-level contents:"
ls -lh python/ | head -30
echo ""
echo "Detailed analysis saved to: python_analysis.txt"
echo ""
echo "To zip for upload:"
echo "  cd python && zip -r9 ../litellm-layer.zip . && cd .."
echo ""
