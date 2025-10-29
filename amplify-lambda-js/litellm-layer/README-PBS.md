# Python LiteLLM Layer Build Guide

This directory contains the optimized build script for creating a minimal Python LiteLLM Lambda layer using **python-build-standalone**.

## Overview

The build process uses **python-build-standalone** to create a relocatable CPython interpreter bundled with a minimal set of LiteLLM dependencies, all packaged into a single Lambda layer that's typically **12-25 MB** (zipped).

### Key Benefits

- **Small size**: 12-25 MB compressed (vs 200+ MB with traditional approaches)
- **Security-first**: Pinned dependencies with optional hash verification
- **Performance**: Pre-compiled, relocatable Python with minimal overhead
- **Minimal attack surface**: Only OpenAI provider included by default

## Build Instructions

### Prerequisites

- `bash` shell
- `curl` for downloading
- `tar` with zstd support
- `zip` for creating final artifact

### Basic Usage

Build for ARM64 (recommended for Lambda):

```bash
cd scripts
./build-python-litellm-layer.sh
```

Build for x86_64:

```bash
ARCH=x86_64 ./build-python-litellm-layer.sh
```

### Build Output

The build creates:

```
layer-build-arm64/
├── layer/
│   └── python/
│       ├── bin/
│       │   └── python3.11
│       └── lib/
│           └── python3.11/
│               ├── site-packages.zip  # All packages in one zip
│               └── sitecustomize.py   # Auto-loads the zip
└── python-litellm-arm64.zip          # Final layer artifact
```

### Size Optimization

The build script aggressively prunes:

1. **Unused providers**: Only `litellm/llms/openai` is kept
2. **Proxy components**: `litellm/proxy` is removed
3. **Test files**: All test directories removed
4. **Documentation**: Docs, examples, and markdown files removed
5. **Type stubs**: `.pyi` files removed
6. **Bytecode**: No `.pyc` files (Lambda loads from zip efficiently)
7. **Standard library**: Unused modules like `tkinter`, `idlelib`, `test`, etc.

## Dependency Management

### Constrained Dependencies

The build uses pinned versions in `constraints.txt`:

```
litellm==1.45.0
httpx==0.27.2
pydantic==2.9.2
pydantic-core==2.23.4
typing-extensions==4.12.2
anyio==4.4.0
sniffio==1.3.1
idna==3.7
certifi==2024.8.30
h11==0.14.0
```

### Adding Azure OpenAI Support

If you need Azure OpenAI, modify the build script:

1. Keep the provider:
```bash
# Comment out this line:
# do rm -rf "${STAGE}/litellm/llms/azure_openai" || true
```

2. Add Azure dependencies to `constraints.txt`:
```
azure-core==1.29.5
azure-identity==1.14.0
```

## Security

### Hash Verification

To verify the Python tarball integrity, set the `PY_TAR_SHA256` environment variable:

```bash
PY_TAR_SHA256="<expected-sha256-hash>" ./build-python-litellm-layer.sh
```

### Supply Chain Security

1. **Pin all dependencies**: All versions are explicitly pinned
2. **Verify hashes**: Use `PY_TAR_SHA256` in CI/CD
3. **Minimal dependencies**: Only essential packages included
4. **SBOM generation**: Document all included packages
5. **Regular updates**: Review and update pinned versions regularly

## Integration

### Serverless Framework

The layer is configured in `amplify-lambda-js/serverless.yml`:

```yaml
provider:
  name: aws
  runtime: nodejs22.x
  architecture: arm64
  environment:
    PYTHONHOME: /opt/python
    PYTHONPATH: /opt/python/lib/python3.11

layers:
  pythonLiteLLM:
    path: ../../layer-build-arm64/layer
    name: ${self:service}-${sls:stage}-python-litellm-arm64
    compatibleRuntimes: [nodejs22.x]
    allowedAccounts: ['*']

functions:
  chat:
    handler: index.handler
    layers:
      - !Ref PythonLiteLLMLambdaLayer
```

### Node.js Usage

Use the Python helper for executing Python code:

```javascript
import { runPython, verifyPythonEnvironment } from './common/pythonExec.js';

// Health check
const info = await verifyPythonEnvironment();
console.log(info); // { python: "3.11.x", litellm: "1.45.0", ... }

// Run Python code
const result = await runPython(`
import json
import litellm
print(json.dumps({"status": "ok", "version": litellm.__version__}))
`);
```

## Troubleshooting

### Build Fails with "tar: Error opening archive"

Ensure you have `zstd` support in tar:
- macOS: `brew install zstd`
- Ubuntu: `apt-get install zstd`

### Lambda Function Can't Find Python

Check that environment variables are set:
```yaml
environment:
  PYTHONHOME: /opt/python
  PYTHONPATH: /opt/python/lib/python3.11
```

### Import Errors for Packages

The packages are in `site-packages.zip`. The `sitecustomize.py` file automatically adds this to the import path. Verify the zip file exists:

```bash
ls -lh layer-build-arm64/layer/python/lib/python3.11/site-packages.zip
```

### Layer Size Still Too Large

1. Check what's taking space:
```bash
cd layer-build-arm64/layer/python
du -sh * | sort -hr | head -20
```

2. Remove additional providers if needed (edit build script)
3. Consider removing more stdlib modules you don't use

## Performance Characteristics

### Cold Start

- **Layer load**: ~100-200ms
- **Python import**: ~50-100ms
- **LiteLLM import**: ~200-300ms
- **Total overhead**: ~350-600ms

### Warm Start

- **Python already loaded**: <10ms overhead
- Persistent Python process eliminates spawn overhead

## Maintenance

### Updating Dependencies

1. Update version pins in build script and `requirements.txt`
2. Test thoroughly in dev environment
3. Generate new SBOM
4. Update `PY_TAR_SHA256` if changing Python version
5. Deploy to staging before production

### Python Version Updates

To update Python version:

1. Check available releases: https://github.com/indygreg/python-build-standalone/releases
2. Update `REL` and `PYVER` variables in build script
3. Update all paths referencing Python version
4. Test compatibility with LiteLLM

## Architecture Support

- **ARM64**: Recommended (better price/performance)
- **x86_64**: Supported (use `ARCH=x86_64`)

Note: Build the correct architecture for your Lambda functions. ARM64 layers won't work with x86_64 functions and vice versa.

## CI/CD Integration

Example GitHub Actions workflow:

```yaml
- name: Build Python Layer
  env:
    ARCH: arm64
    PY_TAR_SHA256: ${{ secrets.PYTHON_TARBALL_SHA256 }}
  run: |
    cd scripts
    ./build-python-litellm-layer.sh

- name: Verify Layer Size
  run: |
    SIZE=$(stat -f%z layer-build-arm64/python-litellm-arm64.zip)
    if [ $SIZE -gt 30000000 ]; then
      echo "Layer too large: $SIZE bytes"
      exit 1
    fi
```

## References

- [python-build-standalone](https://github.com/indygreg/python-build-standalone)
- [Lambda Layers Documentation](https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html)
- [LiteLLM Documentation](https://docs.litellm.ai/)
