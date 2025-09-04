# Lambda Deployment Guide

This guide provides instructions for deploying the Lambda agent loop service using the provided build and deployment scripts.

## Deployment Scripts

### Cached Deployment

Attempt to use this chached deployment script for faster build times. If it fails for some reason, you can revert to the standard deploy below. 

```bash
./deploy-cached.sh [stage] [region]
```

Example:
```bash
./deploy-cached.sh dev us-east-1
```

This script:
1. Builds a new container image with a timestamp tag
2. Utilizes Docker layer caching for faster builds
3. Deploys using serverless framework with the `--force` flag
4. Cleans up the timestamp file for next deployment

Use the cached deployment when you want to speed up the build process during iterative development.

### Standard Deployment

```bash
./deploy.sh [stage] [region]
```

Example:
```bash
./deploy.sh dev us-east-1
```

This script:
1. Builds a new container image with a timestamp tag
2. Pushes the image to ECR
3. Deploys using serverless framework with the `--force` flag
4. Cleans up the timestamp file for next deployment



## Linux Requirements Generation

The `generate-linux-lock.sh` script creates a Linux-compatible requirements file from Poetry:

```bash
./generate-linux-lock.sh
```

### What This Script Does

1. Runs a Docker container with AWS Lambda's Python runtime
2. Installs Poetry inside the container
3. Generates a Poetry lock file specifically for Linux environment
4. Extracts all package dependencies from the lock file
5. Filters out Windows-specific packages that would cause issues
6. Creates `agent-requirements-fat-linux-clean.txt` with clean Linux dependencies

### When to Use It

Run this script when:
- You've updated `pyproject.toml` with new dependencies
- You need to ensure all dependencies are Linux-compatible for Lambda
- You're experiencing package compatibility issues in the Lambda environment

After running, you can update your requirements file:
```bash
cp agent-requirements-fat-linux-clean.txt agent-requirements-fat.txt
```

## Container Image Updates

The deployment system uses timestamp-based image tags to ensure Lambda always uses the latest container:

- Each build generates a unique timestamp (e.g., `dev-agent-router-1690123456`)
- The timestamp is exported to `deploy-timestamp.env`
- Serverless.yml references this timestamp in image URIs
- Deployment script cleans up the timestamp file after deployment

This prevents the issue where Lambda might continue using an old container image.

## Memory Configuration

All container-based Lambda functions are configured with 4096MB memory to improve:
- Cold start performance
- Initialization time
- Overall execution speed

This setting can be adjusted in `serverless.yml` if needed.

## SQLite Compatibility

The deployment automatically handles SQLite compatibility for Chroma:
1. Installs `pysqlite3-binary` in the container
2. Patches Python's sqlite3 module at runtime in `service/core.py`
3. Ensures Chroma has access to SQLite version ≥ 3.35.0

## Troubleshooting

### Container Not Updating
- Ensure the timestamp file is generated during build
- Check ECR for the timestamped images
- Verify serverless.yml references `${self:custom.deployTimestamp}`

### Initialization Timeout
- Try increasing memory allocation further
- Consider using provisioned concurrency for production
- Split into multiple smaller containers

### Package Incompatibilities
- Re-run `./generate-linux-lock.sh` to generate fresh Linux requirements
- Check logs for specific package errors
- Consider alternative packages or versions for problematic dependencies