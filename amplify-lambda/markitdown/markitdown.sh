#!/bin/bash
set -e

# Install dependencies with minimal packages

docker run --rm --entrypoint pip -v $(pwd):/var/task -w /var/task public.ecr.aws/lambda/python:3.11 install --no-cache-dir --upgrade -r requirements.txt -t ./python --platform manylinux2014_x86_64 --only-binary=:all:

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