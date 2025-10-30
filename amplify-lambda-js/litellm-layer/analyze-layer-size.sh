#!/bin/bash
set -e

echo "======================================"
echo "Lambda Layer Size Analysis"
echo "======================================"

if [ ! -d "python" ]; then
    echo "ERROR: python/ directory not found. Run build-layer.sh first."
    exit 1
fi

REPORT="layer-size-analysis.txt"
echo "Lambda Layer Size Analysis - $(date)" > $REPORT
echo "=====================================" >> $REPORT
echo "" >> $REPORT

# Current total size
TOTAL_SIZE=$(du -sh python | cut -f1)
echo "Current total size: $TOTAL_SIZE" | tee -a $REPORT
echo "" >> $REPORT

echo "Analyzing removable content..." | tee -a $REPORT
echo "" >> $REPORT

# 1. __pycache__ directories
echo "1. __pycache__ directories:" | tee -a $REPORT
PYCACHE_SIZE=$(du -ch $(find python -type d -name "__pycache__" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
PYCACHE_COUNT=$(find python -type d -name "__pycache__" 2>/dev/null | wc -l)
echo "   Size: $PYCACHE_SIZE" | tee -a $REPORT
echo "   Count: $PYCACHE_COUNT directories" | tee -a $REPORT
echo "" >> $REPORT

# 2. Compiled Python files
echo "2. Compiled Python files (.pyc, .pyo, .pyd):" | tee -a $REPORT
PYC_SIZE=$(find python \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pyd" \) -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
PYC_COUNT=$(find python \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pyd" \) 2>/dev/null | wc -l)
echo "   Size: $PYC_SIZE" | tee -a $REPORT
echo "   Count: $PYC_COUNT files" | tee -a $REPORT
echo "" >> $REPORT

# 3. Package metadata
echo "3. Package metadata (.dist-info, .egg-info):" | tee -a $REPORT
META_SIZE=$(du -ch $(find python -type d \( -name "*.dist-info" -o -name "*.egg-info" \) 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
META_COUNT=$(find python -type d \( -name "*.dist-info" -o -name "*.egg-info" \) 2>/dev/null | wc -l)
echo "   Size: $META_SIZE" | tee -a $REPORT
echo "   Count: $META_COUNT directories" | tee -a $REPORT
if [ $META_COUNT -gt 0 ]; then
    echo "   Packages:" >> $REPORT
    find python -type d \( -name "*.dist-info" -o -name "*.egg-info" \) 2>/dev/null | sed 's/.*\//   - /' >> $REPORT
fi
echo "" >> $REPORT

# 4. Documentation directories
echo "4. Documentation directories (doc, docs):" | tee -a $REPORT
DOC_SIZE=$(du -ch $(find python -type d \( -name "doc" -o -name "docs" \) 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
DOC_COUNT=$(find python -type d \( -name "doc" -o -name "docs" \) 2>/dev/null | wc -l)
echo "   Size: $DOC_SIZE" | tee -a $REPORT
echo "   Count: $DOC_COUNT directories" | tee -a $REPORT
if [ $DOC_COUNT -gt 0 ]; then
    echo "   Locations:" >> $REPORT
    find python -type d \( -name "doc" -o -name "docs" \) 2>/dev/null | sed 's/python\//   - /' >> $REPORT
fi
echo "" >> $REPORT

# 5. Examples
echo "5. Examples directories:" | tee -a $REPORT
EXAMPLES_SIZE=$(du -ch $(find python -type d \( -name "examples" -o -name "example" \) 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
EXAMPLES_COUNT=$(find python -type d \( -name "examples" -o -name "example" \) 2>/dev/null | wc -l)
echo "   Size: $EXAMPLES_SIZE" | tee -a $REPORT
echo "   Count: $EXAMPLES_COUNT directories" | tee -a $REPORT
if [ $EXAMPLES_COUNT -gt 0 ]; then
    echo "   Locations:" >> $REPORT
    find python -type d \( -name "examples" -o -name "example" \) 2>/dev/null | sed 's/python\//   - /' >> $REPORT
fi
echo "" >> $REPORT

# 6. Test directories
echo "6. Test directories:" | tee -a $REPORT
TEST_SIZE=$(du -ch $(find python -type d \( -name "tests" -o -name "test" -o -name "testing" \) 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
TEST_COUNT=$(find python -type d \( -name "tests" -o -name "test" -o -name "testing" \) 2>/dev/null | wc -l)
echo "   Size: $TEST_SIZE" | tee -a $REPORT
echo "   Count: $TEST_COUNT directories" | tee -a $REPORT
if [ $TEST_COUNT -gt 0 ]; then
    echo "   Locations (first 20):" >> $REPORT
    find python -type d \( -name "tests" -o -name "test" -o -name "testing" \) 2>/dev/null | head -20 | sed 's/python\//   - /' >> $REPORT
fi
echo "" >> $REPORT

# 7. Documentation files
echo "7. Documentation files (.md, .txt, .rst, LICENSE, etc.):" | tee -a $REPORT
DOC_FILES_SIZE=$(find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" -o -name "LICENSE*" -o -name "COPYING*" -o -name "AUTHORS*" -o -name "CHANGELOG*" -o -name "HISTORY*" \) -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
DOC_FILES_COUNT=$(find python -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" -o -name "LICENSE*" -o -name "COPYING*" -o -name "AUTHORS*" -o -name "CHANGELOG*" -o -name "HISTORY*" \) 2>/dev/null | wc -l)
echo "   Size: $DOC_FILES_SIZE" | tee -a $REPORT
echo "   Count: $DOC_FILES_COUNT files" | tee -a $REPORT
echo "" >> $REPORT

# 8. Type stub files
echo "8. Type stub files (.pyi):" | tee -a $REPORT
PYI_SIZE=$(find python -name "*.pyi" -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
PYI_COUNT=$(find python -name "*.pyi" 2>/dev/null | wc -l)
echo "   Size: $PYI_SIZE" | tee -a $REPORT
echo "   Count: $PYI_COUNT files" | tee -a $REPORT
echo "" >> $REPORT

# 9. Benchmark directories
echo "9. Benchmark directories:" | tee -a $REPORT
BENCH_SIZE=$(du -ch $(find python -type d -name "benchmark*" 2>/dev/null) 2>/dev/null | grep total | cut -f1 || echo "0")
BENCH_COUNT=$(find python -type d -name "benchmark*" 2>/dev/null | wc -l)
echo "   Size: $BENCH_SIZE" | tee -a $REPORT
echo "   Count: $BENCH_COUNT directories" | tee -a $REPORT
echo "" >> $REPORT

# 10. C source files
echo "10. C/C++ source files (.c, .h, .cpp):" | tee -a $REPORT
C_SIZE=$(find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
C_COUNT=$(find python -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.cc" \) 2>/dev/null | wc -l)
echo "   Size: $C_SIZE" | tee -a $REPORT
echo "   Count: $C_COUNT files" | tee -a $REPORT
echo "" >> $REPORT

# 11. Config files
echo "11. Version control and config files (.git*, .cfg, .ini, .toml, .yaml):" | tee -a $REPORT
CONFIG_SIZE=$(find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" -o -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) -exec du -ch {} + 2>/dev/null | grep total | cut -f1 || echo "0")
CONFIG_COUNT=$(find python -type f \( -name ".git*" -o -name ".coveragerc" -o -name ".pylintrc" -o -name "*.cfg" -o -name "*.ini" -o -name "*.toml" -o -name "*.yaml" -o -name "*.yml" \) 2>/dev/null | wc -l)
echo "   Size: $CONFIG_SIZE" | tee -a $REPORT
echo "   Count: $CONFIG_COUNT files" | tee -a $REPORT
echo "" >> $REPORT

# 12. Build tools (pip, setuptools, wheel)
echo "12. Build tools (pip, setuptools, wheel):" | tee -a $REPORT
BUILD_SIZE=$(du -ch python/pip* python/setuptools* python/wheel* python/_distutils_hack 2>/dev/null | grep total | cut -f1 || echo "0")
BUILD_DIRS=$(ls -d python/pip* python/setuptools* python/wheel* python/_distutils_hack 2>/dev/null | wc -l)
echo "   Size: $BUILD_SIZE" | tee -a $REPORT
echo "   Directories: $BUILD_DIRS" | tee -a $REPORT
echo "" >> $REPORT

# Summary
echo "=====================================" | tee -a $REPORT
echo "REMOVABLE CONTENT SUMMARY" | tee -a $REPORT
echo "=====================================" | tee -a $REPORT
echo "" >> $REPORT
echo "Category                          Size        Count" | tee -a $REPORT
echo "---------------------------------------------------" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "__pycache__" "$PYCACHE_SIZE" "$PYCACHE_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Compiled files (.pyc/.pyo)" "$PYC_SIZE" "$PYC_COUNT files" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Package metadata" "$META_SIZE" "$META_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Documentation dirs" "$DOC_SIZE" "$DOC_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Examples" "$EXAMPLES_SIZE" "$EXAMPLES_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Tests" "$TEST_SIZE" "$TEST_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Doc files (.md/.txt/.rst)" "$DOC_FILES_SIZE" "$DOC_FILES_COUNT files" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Type stubs (.pyi)" "$PYI_SIZE" "$PYI_COUNT files" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Benchmarks" "$BENCH_SIZE" "$BENCH_COUNT dirs" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "C/C++ source" "$C_SIZE" "$C_COUNT files" | tee -a $REPORT
printf "%-30s %-10s %-10s\n" "Config files" "$CONFIG_SIZE" "$CONFIG_COUNT files" | tee -a $REPORT
printf "%-30s %-10s\n" "Build tools (pip/setuptools)" "$BUILD_SIZE" | tee -a $REPORT
echo "" >> $REPORT

# Largest packages
echo "=====================================" | tee -a $REPORT
echo "LARGEST PACKAGES (Top 30)" | tee -a $REPORT
echo "=====================================" | tee -a $REPORT
du -h python/* 2>/dev/null | sort -rh | head -30 | tee -a $REPORT
echo "" >> $REPORT

# Largest .so files
echo "=====================================" | tee -a $REPORT
echo "LARGEST .so FILES (Top 20)" | tee -a $REPORT
echo "=====================================" | tee -a $REPORT
find python -name "*.so*" -type f -exec du -h {} \; 2>/dev/null | sort -rh | head -20 | tee -a $REPORT
echo "" >> $REPORT

echo "======================================"
echo "Analysis complete!"
echo "======================================"
echo "Report saved to: $REPORT"
echo ""
echo "Current total size: $TOTAL_SIZE"
echo ""
echo "To apply optimizations, run:"
echo "  ./build-layer-optimized.sh"
echo ""
