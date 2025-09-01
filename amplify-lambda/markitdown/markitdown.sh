#!/bin/bash
set -e

docker run --rm --entrypoint pip -v $(pwd):/var/task -w /var/task public.ecr.aws/lambda/python:3.11 install --no-cache-dir --upgrade -r requirements.txt -t ./python --platform manylinux2014_x86_64 --only-binary=:all:



# Remove audio/video processing libraries (only if they exist and are clearly not needed)
rm -rf python/speech_recognition* || true
rm -rf python/pydub* || true
rm -rf python/youtube_transcript_api* || true

# Remove only clearly unused data science libraries
rm -rf python/joblib* || true


# Manually download and extract minimal pycommon modules
echo "Downloading pycommon source code..."
# Clone to a local temporary directory instead of inside Docker
rm -rf ./tmp_pycommon
git clone --depth 1 --branch v0.1.0 https://github.com/gaiin-platform/pycommon.git ./tmp_pycommon

# Create pycommon package structure in python directory
echo "Extracting minimal pycommon modules..."
mkdir -p python/pycommon/api
mkdir -p python/pycommon/llm

# Copy only the specific files we need
if [ -d "./tmp_pycommon/pycommon/api" ]; then
    echo "Copying API modules..."
    # Copy only the API modules we need
    cp ./tmp_pycommon/pycommon/api/secrets.py python/pycommon/api/ 2>/dev/null || echo "secrets.py not found"
    cp ./tmp_pycommon/pycommon/api/models.py python/pycommon/api/ 2>/dev/null || echo "models.py not found"
    cp ./tmp_pycommon/pycommon/api/get_endpoint.py python/pycommon/api/ 2>/dev/null || echo "get_endpoint.py not found"
    
    # Create minimal __init__.py for api module (overwrite the one from GitHub)
    cat > python/pycommon/api/__init__.py << 'EOF'
# Minimal API module exports for RAG functionality
# Only import the modules that were actually copied

from .secrets import (
    delete_secret_parameter,
    get_secret_parameter,
    store_secret_parameter,
)

# Import other copied modules
from . import models
from . import get_endpoint

__all__ = [
    "get_secret_parameter",
    "store_secret_parameter", 
    "delete_secret_parameter",
    "models",
    "get_endpoint",
]
EOF
    echo "API modules copied with minimal __init__.py"
fi

if [ -d "./tmp_pycommon/pycommon/llm" ]; then
    echo "Copying LLM modules..."
    # Copy the entire llm folder since chat.py might depend on other modules
    cp -r ./tmp_pycommon/pycommon/llm/* python/pycommon/llm/ 2>/dev/null || echo "llm folder not found"
    # Ensure __init__.py exists
    touch python/pycommon/llm/__init__.py
    echo "LLM modules copied"
fi

# Copy encoders.py as a single file
if [ -f "./tmp_pycommon/pycommon/encoders.py" ]; then
    echo "Copying encoders.py..."
    cp ./tmp_pycommon/pycommon/encoders.py python/pycommon/ 2>/dev/null || echo "encoders.py not found"
    echo "Encoders module copied"
fi

# Copy const.py as a single file
if [ -f "./tmp_pycommon/pycommon/const.py" ]; then
    echo "Copying const.py..."
    cp ./tmp_pycommon/pycommon/const.py python/pycommon/ 2>/dev/null || echo "const.py not found"
    echo "Const module copied"
fi

# Create minimal main __init__.py (overwrite the one from GitHub)
cat > python/pycommon/__init__.py << 'EOF'
# Minimal pycommon module exports for RAG functionality
# Only import the modules that were actually copied

from . import api
from . import llm
from . import encoders
from . import const

__all__ = [
    "api",
    "llm",
    "encoders",
    "const",
]
EOF

# Clean up temporary pycommon download
rm -rf ./tmp_pycommon

echo "Pycommon minimal modules extracted successfully"

# List what we actually copied for verification
echo "Verifying copied files:"
find python/pycommon -name "*.py" -type f | head -20

# Remove unnecessary files
find python -type d -name "__pycache__" -exec rm -rf {} +
find python -type d -name "*.dist-info" -exec rm -rf {} +
find python -type d -name "*.egg-info" -exec rm -rf {} +
find python -name "*.pyc" -delete
find python -name "*.pyo" -delete
find python -name "*.pyd" -delete

# Only keep documentation if specifically needed
find python -type d -name "doc" -exec rm -rf {} +
find python -type d -name "docs" -exec rm -rf {} +
find python -type d -name "examples" -exec rm -rf {} +
find python -type d -name "tests" -exec rm -rf {} +
find python -type d -name "test" -exec rm -rf {} +

echo "Layer build complete: requirements.txt packages + pycommon source files only"