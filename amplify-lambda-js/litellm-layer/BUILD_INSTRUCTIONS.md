# Build Instructions - Python LiteLLM Layer

## Quick Start

```bash
# Navigate to this directory
cd amplify-lambda-js/litellm-layer

# Build the layer
./build-layer.sh

# Deploy
cd ..
serverless deploy --stage dev
```

## What This Builds

A minimal Python 3.11 Lambda layer with LiteLLM that:
- **Size**: 12-25 MB (compressed) vs 200+ MB before
- **Cold Start**: ~500-600ms vs ~3.5s before
- **Architecture**: ARM64 for better performance
- **Security**: Pinned dependencies, minimal attack surface

## Build Output

```
amplify-lambda-js/litellm-layer/
└── layer-build-arm64/
    ├── layer/                      # Used by serverless.yml
    │   └── python/
    │       ├── bin/python3.11
    │       └── lib/python3.11/
    │           ├── site-packages.zip
    │           └── sitecustomize.py
    └── python-litellm-arm64.zip    # Final artifact
```

## Prerequisites

The build script checks these automatically:
- `curl` - for downloading Python
- `tar` with `zstd` support - for extraction
- `zip` - for packaging

If missing on macOS: `brew install zstd`

## Build Options

### ARM64 (Default - Recommended)
```bash
./build-layer.sh
```

### x86_64
```bash
ARCH=x86_64 ./build-python-litellm-layer.sh
```

### With Hash Verification (CI/CD)
```bash
PY_TAR_SHA256="your-sha256-hash" ./build-python-litellm-layer.sh
```

## Files in This Directory

### Build Scripts
- **build-layer.sh** - Main build wrapper (use this)
- **build-python-litellm-layer.sh** - Core build logic
- **test-build-prerequisites.sh** - Prerequisite checker

### Documentation
- **BUILD_INSTRUCTIONS.md** - This file
- **QUICK_START_PBS.md** - Quick reference guide
- **README-PBS.md** - Comprehensive documentation
- **IMPLEMENTATION_SUMMARY.md** - Technical details
- **CHANGES.md** - Change summary

### Configuration
- **requirements.txt** - Pinned Python dependencies

### Legacy Build Scripts (for reference)
- build-layer-*.sh - Previous build attempts
- Other documentation files

## Troubleshooting

### Build fails with "tar: Error opening archive"
```bash
brew install zstd  # macOS
apt-get install zstd  # Ubuntu
```

### Layer too large (>30 MB)
Check what's using space:
```bash
cd layer-build-arm64/layer/python
du -sh * | sort -hr | head -20
```

### Runtime errors after deployment
1. Check `serverless.yml` has correct layer path: `litellm-layer/layer-build-arm64/layer`
2. Verify environment variables are set:
   ```yaml
   environment:
     PYTHONHOME: /opt/python
     PYTHONPATH: /opt/python/lib/python3.11
   ```

## Testing

After building:

```bash
# Check size
ls -lh layer-build-arm64/python-litellm-arm64.zip

# Verify Python works
layer-build-arm64/layer/python/bin/python3.11 --version

# Test litellm import
layer-build-arm64/layer/python/bin/python3.11 -c "import litellm; print(litellm.__version__)"
```

## Deployment

The serverless.yml is already configured. Just deploy:

```bash
cd ../amplify-lambda-js
serverless deploy --stage dev
```

Monitor CloudWatch logs for any issues.

## Support

- **Quick help**: See QUICK_START_PBS.md
- **Full guide**: See README-PBS.md
- **Technical details**: See IMPLEMENTATION_SUMMARY.md
- **What changed**: See CHANGES.md
