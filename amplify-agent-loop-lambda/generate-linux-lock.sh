#!/bin/bash

# Script to generate Linux-only Poetry lock file
echo "Generating Linux-only Poetry lock file..."

# Run Poetry in Linux container with platform constraints
docker run --rm --entrypoint="" \
  -e POETRY_CACHE_DIR=/tmp/poetry_cache \
  -v $(pwd):/app -w /app \
  public.ecr.aws/lambda/python:3.11 bash -c "
# Install Poetry
echo 'Installing Poetry...'
pip install poetry

# Verify Poetry installation
poetry --version

# Configure Poetry for Linux
echo 'Configuring Poetry...'
poetry config virtualenvs.create false
poetry config installer.parallel true  
poetry config solver.lazy-wheel true

# Generate lock file
echo 'Generating new poetry.lock...'
poetry lock --no-update

# Extract clean Linux packages
python3 -c '
import re
import sys

print(\"Processing poetry.lock...\")

with open(\"poetry.lock\", \"r\") as f:
    content = f.read()

# Define Windows-specific packages to exclude
windows_packages = {
    \"pywin32\", \"pywinpty\", \"win-unicode-console\", \"win32-setctime\", 
    \"pywin32-ctypes\", \"colorama\", \"windows-curses\", \"wincertstore\"
}

# Extract all packages
matches = re.findall(r\"name = \\\"([^\\\"]+)\\\"\\nversion = \\\"([^\\\"]+)\\\"\", content)

# Filter out Windows packages (case-insensitive)
linux_packages = []
excluded_packages = []

for name, version in matches:
    if name.lower() in windows_packages or \"win\" in name.lower():
        excluded_packages.append(f\"{name}=={version}\")
    else:
        linux_packages.append((name, version))

# Write clean requirements
with open(\"agent-requirements-fat-linux-clean.txt\", \"w\") as f:
    for name, version in linux_packages:
        f.write(f\"{name}=={version}\\n\")

print(f\"✅ Generated {len(linux_packages)} Linux-compatible packages\")
print(f\"❌ Excluded {len(excluded_packages)} Windows packages:\")
for pkg in excluded_packages:
    print(f\"   - {pkg}\")
'
"

echo "✅ Linux-only lock file generated: agent-requirements-fat-linux-clean.txt"
echo "To use it: cp agent-requirements-fat-linux-clean.txt agent-requirements-fat.txt" 