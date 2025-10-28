#!/bin/bash
set -e

echo "======================================"
echo "Building ULTRA-SLIM Python LiteLLM Lambda Layer"
echo "Target: Remove 50MB+ from standard build"
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
INITIAL_SIZE=$(du -sb python | cut -f1)
echo "Initial size after pip install: $((INITIAL_SIZE / 1024 / 1024))MB"

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

echo ""
echo "======================================"
echo "ULTRA-SLIM OPTIMIZATION (Target: -50MB)"
echo "======================================"

# Track savings
SAVINGS_LOG="ultra_slim_savings.txt"
echo "Ultra-Slim Optimization Report" > $SAVINGS_LOG
echo "==============================" >> $SAVINGS_LOG
echo "" >> $SAVINGS_LOG

# PHASE 1: Standard cleanup (10-15MB)
echo "Phase 1: Standard cleanup..."
find python -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find python -name "*.pyc" -delete 2>/dev/null || true
find python -name "*.pyo" -delete 2>/dev/null || true
find python -name "*.pyd" -delete 2>/dev/null || true
AFTER_PHASE1=$(du -sb python | cut -f1)
PHASE1_SAVED=$(((INITIAL_SIZE - AFTER_PHASE1) / 1024 / 1024))
echo "  Saved: ${PHASE1_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 2: Remove all metadata, docs, tests (15-20MB)
echo "Phase 2: Removing metadata, docs, and tests..."
find python -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "doc" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "example" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "testing" -exec rm -rf {} + 2>/dev/null || true
find python -type d -name "benchmark*" -exec rm -rf {} + 2>/dev/null || true
AFTER_PHASE2=$(du -sb python | cut -f1)
PHASE2_SAVED=$(((AFTER_PHASE1 - AFTER_PHASE2) / 1024 / 1024))
echo "  Saved: ${PHASE2_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 3: Remove all documentation files (5-10MB)
echo "Phase 3: Removing documentation files..."
find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" \) -delete 2>/dev/null || true
find python -type f -name "LICENSE*" -delete 2>/dev/null || true
find python -type f -name "COPYING*" -delete 2>/dev/null || true
find python -type f -name "AUTHORS*" -delete 2>/dev/null || true
find python -type f -name "CHANGELOG*" -delete 2>/dev/null || true
find python -type f -name "HISTORY*" -delete 2>/dev/null || true
find python -type f -name "NEWS*" -delete 2>/dev/null || true
find python -name "*.pyi" -delete 2>/dev/null || true
AFTER_PHASE3=$(du -sb python | cut -f1)
PHASE3_SAVED=$(((AFTER_PHASE2 - AFTER_PHASE3) / 1024 / 1024))
echo "  Saved: ${PHASE3_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 4: Remove build tools (10-15MB)
echo "Phase 4: Removing build tools..."
rm -rf python/pip* python/setuptools* python/wheel* python/_distutils_hack 2>/dev/null || true
rm -rf python/pkg_resources/tests 2>/dev/null || true
AFTER_PHASE4=$(du -sb python | cut -f1)
PHASE4_SAVED=$(((AFTER_PHASE3 - AFTER_PHASE4) / 1024 / 1024))
echo "  Saved: ${PHASE4_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 5: Remove C source files (3-8MB)
echo "Phase 5: Removing C/C++ source files..."
find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) -delete 2>/dev/null || true
AFTER_PHASE5=$(du -sb python | cut -f1)
PHASE5_SAVED=$(((AFTER_PHASE4 - AFTER_PHASE5) / 1024 / 1024))
echo "  Saved: ${PHASE5_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 6: Remove config and VCS files (1-3MB)
echo "Phase 6: Removing config and VCS files..."
find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" \) -delete 2>/dev/null || true
find python -type f \( -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) -delete 2>/dev/null || true
AFTER_PHASE6=$(du -sb python | cut -f1)
PHASE6_SAVED=$(((AFTER_PHASE5 - AFTER_PHASE6) / 1024 / 1024))
echo "  Saved: ${PHASE6_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 7: Strip debug symbols from .so files (5-15MB) - BIG WIN
echo "Phase 7: Stripping debug symbols from .so files..."
SO_BEFORE=$(du -sb python | cut -f1)
find python -name "*.so*" -type f -exec strip --strip-debug {} \; 2>/dev/null || true
SO_AFTER=$(du -sb python | cut -f1)
PHASE7_SAVED=$(((SO_BEFORE - SO_AFTER) / 1024 / 1024))
echo "  Saved: ${PHASE7_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 8: Remove typing-related packages if present (2-5MB)
echo "Phase 8: Removing type-checking packages..."
rm -rf python/mypy* python/typing_inspect python/typing_extensions/tests 2>/dev/null || true
AFTER_PHASE8=$(du -sb python | cut -f1)
PHASE8_SAVED=$(((SO_AFTER - AFTER_PHASE8) / 1024 / 1024))
echo "  Saved: ${PHASE8_SAVED}MB" | tee -a $SAVINGS_LOG

# PHASE 9: Find and report largest remaining packages for manual review
echo ""
echo "Phase 9: Analyzing largest remaining packages..."
echo "" >> $SAVINGS_LOG
echo "Largest Packages (Top 20):" >> $SAVINGS_LOG
du -h python/* 2>/dev/null | sort -rh | head -20 | tee -a $SAVINGS_LOG

# Calculate final size and total savings
FINAL_SIZE=$(du -sb python | cut -f1)
TOTAL_SAVED=$(((INITIAL_SIZE - FINAL_SIZE) / 1024 / 1024))
FINAL_SIZE_MB=$((FINAL_SIZE / 1024 / 1024))

echo ""
echo "======================================"
echo "OPTIMIZATION COMPLETE"
echo "======================================"
echo ""
printf "%-25s %10s MB\n" "Initial size:" "$((INITIAL_SIZE / 1024 / 1024))"
printf "%-25s %10s MB\n" "Final size:" "$FINAL_SIZE_MB"
printf "%-25s %10s MB (%.1f%%)\n" "Total saved:" "$TOTAL_SAVED" "$(echo "scale=1; $TOTAL_SAVED * 100 / ($INITIAL_SIZE / 1024 / 1024)" | bc)"
echo ""
echo "Breakdown by phase:"
printf "  Phase 1 (Caches):       %4s MB\n" "$PHASE1_SAVED"
printf "  Phase 2 (Tests/Docs):   %4s MB\n" "$PHASE2_SAVED"
printf "  Phase 3 (Doc files):    %4s MB\n" "$PHASE3_SAVED"
printf "  Phase 4 (Build tools):  %4s MB\n" "$PHASE4_SAVED"
printf "  Phase 5 (C sources):    %4s MB\n" "$PHASE5_SAVED"
printf "  Phase 6 (Config files): %4s MB\n" "$PHASE6_SAVED"
printf "  Phase 7 (.so strip):    %4s MB ⭐ BIGGEST SAVER\n" "$PHASE7_SAVED"
printf "  Phase 8 (Type pkgs):    %4s MB\n" "$PHASE8_SAVED"
echo ""

# Check if we hit our target
if [ $TOTAL_SAVED -ge 50 ]; then
    echo "✅ SUCCESS! Saved ${TOTAL_SAVED}MB (target was 50MB)"
else
    echo "⚠️  Saved ${TOTAL_SAVED}MB (target was 50MB)"
    echo ""
    echo "Additional options to consider:"
    echo "  1. Review largest packages above"
    echo "  2. Remove unused LiteLLM providers (see below)"
    echo "  3. Consider using --no-deps for some packages"
fi

echo ""
echo "Full report saved to: $SAVINGS_LOG"
echo ""
echo "To deploy:"
echo "  cd .. && serverless deploy --stage dev"
echo ""
