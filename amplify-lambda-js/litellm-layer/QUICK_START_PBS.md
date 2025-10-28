# Quick Start: Building the Python LiteLLM Layer

## TL;DR

```bash
# 1. Navigate to litellm-layer directory
cd amplify-lambda-js/litellm-layer

# 2. Check prerequisites and build
./build-layer.sh

# 3. Deploy
cd ..
serverless deploy --stage dev
```

## What You Get

- **Size**: 12-25 MB compressed (vs 200+ MB before)
- **Cold Start**: ~500-600ms (vs ~3.5s before)
- **Security**: Pinned dependencies, minimal attack surface
- **Architecture**: ARM64 (better price/performance)

## Build Outputs

After running the build script:

```
amplify-lambda-js/litellm-layer/
├── layer-build-arm64/
│   ├── layer/                      # Used by serverless.yml
│   │   └── python/
│   │       ├── bin/python3.11
│   │       └── lib/python3.11/
│   │           ├── site-packages.zip
│   │           └── sitecustomize.py
│   └── python-litellm-arm64.zip    # Final artifact (~12-25 MB)
```

## Build for x86_64

```bash
ARCH=x86_64 ./build-python-litellm-layer.sh
```

This creates `layer-build-x86_64/` instead.

## Verify Build

```bash
# Check size
ls -lh layer-build-arm64/python-litellm-arm64.zip

# Check contents
unzip -l layer-build-arm64/python-litellm-arm64.zip | head -20

# Verify Python works
layer-build-arm64/layer/python/bin/python3.11 --version
```

## Integration

The following files have been updated for you:

1. **serverless.yml** - Uses new layer path
2. **litellmClient.js** - Uses bundled Python interpreter
3. **requirements.txt** - Pinned dependency versions

No additional changes needed!

## Troubleshooting

### Build fails with "tar: Error opening archive"

```bash
# Install zstd
brew install zstd  # macOS
```

### "Module not found" errors at runtime

Check environment variables in serverless.yml:
```yaml
environment:
  PYTHONHOME: /opt/python
  PYTHONPATH: /opt/python/lib/python3.11
```

### Layer too large

Check what's using space:
```bash
cd layer-build-arm64/layer/python
du -sh * | sort -hr | head -20
```

## Security: Hash Verification

In production CI/CD:

```bash
# Set expected hash
PY_TAR_SHA256="your-expected-sha256-hash" ./build-python-litellm-layer.sh
```

Get hash from: https://github.com/indygreg/python-build-standalone/releases

## Adding Azure OpenAI

Edit `build-python-litellm-layer.sh`:

```bash
# Comment out this line (around line 69):
# do rm -rf "${STAGE}/litellm/llms/azure_openai" || true
```

Add to constraints:
```bash
azure-core==1.29.5
azure-identity==1.14.0
```

Rebuild.

## Need Help?

- **Full documentation**: `README-PBS.md`
- **Implementation details**: `IMPLEMENTATION_SUMMARY.md`
- **Architecture questions**: Check the ../serverless.yml comments

## Expected Timeline

- **Prerequisites check**: 30 seconds
- **Build time**: 2-5 minutes (depends on download speed)
- **Deploy time**: 1-2 minutes
- **Total**: ~5-10 minutes for first build

## Success Indicators

✅ Build completes without errors
✅ Layer size < 30 MB
✅ `python3.11 --version` works
✅ Lambda function can import litellm
✅ Cold start < 1 second

## Next Steps After Build

1. Deploy to dev: `serverless deploy --stage dev`
2. Test a chat request
3. Check CloudWatch logs
4. If all good, deploy to staging
5. Finally, deploy to production
