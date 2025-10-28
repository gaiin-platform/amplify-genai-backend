# Python LiteLLM Lambda Layer

This Lambda layer provides Python 3.11 runtime with LiteLLM and boto3 for the Node.js Lambda function.

## Overview

The `amplify-lambda-js` service is a **Node.js 22.x Lambda** that needs to spawn Python subprocesses to run LiteLM for unified LLM provider access. This layer provides:

- Python 3.11 binary at `/opt/python/bin/python3`
- LiteLLM 1.78.7 and all dependencies
- boto3 for AWS SDK access

## 🚀 Quick Start

### Standard Build (~117MB)
```bash
cd litellm-layer
./build-layer.sh
```

### **Optimized Build (~60-75MB)** ⭐ Recommended
```bash
cd litellm-layer
./build-layer-optimized.sh
```

**The optimized build removes 40-60MB (35-50%) by removing documentation, tests, examples, and other non-runtime files. See [SIZE_REDUCTION_SUMMARY.md](SIZE_REDUCTION_SUMMARY.md) for details.**

## Build Options

### 1. Standard Build (Original)
```bash
./build-layer.sh
```
- Size: ~117MB uncompressed
- Basic cleanup only
- Fastest build time

### 2. Optimized Build (Recommended)
```bash
./build-layer-optimized.sh
```
- Size: ~60-75MB uncompressed (35-50% smaller)
- Aggressive cleanup of non-runtime files
- Detailed analysis report
- **Same functionality, smaller size**

### 3. Analyze Existing Build
```bash
./analyze-layer-size.sh
```
- Analyzes what can be removed from existing `python/` directory
- Generates detailed size breakdown report
- Helps understand optimization opportunities

### 4. Compare Standard vs Optimized
```bash
./compare-builds.sh
```
- Builds both versions side-by-side
- Shows exact size differences
- Displays file count comparisons
- Takes 4-6 minutes

## Prerequisites
- Docker installed and running
- Bash shell

## Documentation

| File | Description |
|------|-------------|
| **[QUICK_START.md](QUICK_START.md)** | One-page quick reference |
| **[SIZE_REDUCTION_SUMMARY.md](SIZE_REDUCTION_SUMMARY.md)** | Detailed breakdown of size savings |
| **[OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md)** | Complete guide on optimization techniques |
| **README.md** (this file) | Main documentation |

## Deployment

The layer is automatically deployed when you run:

```bash
serverless deploy --stage dev
```

The `serverless.yml` configuration:

```yaml
layers:
  pythonLiteLLM:
    path: litellm-layer
    name: ${self:service}-${sls:stage}-python-litellm
    description: Python 3.11 runtime with LiteLLM and boto3 for Node.js Lambda
    compatibleRuntimes:
      - nodejs22.x

functions:
  chat:
    handler: index.handler
    layers:
      - !Ref PythonLiteLLMLambdaLayer
```

## How It Works

### In Node.js Lambda

The `litellmClient.js` module detects the Lambda environment and uses the layer's Python:

```javascript
// Determine Python path based on environment
const isLambda = !!process.env.LAMBDA_TASK_ROOT || !!process.env.AWS_EXECUTION_ENV;
const pythonPath = isLambda ? '/opt/python/bin/python3' : 'python3';

globalPythonProcess = spawn(pythonPath, [pythonScriptPath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
        ...process.env,
        PYTHONPATH: isLambda ? '/opt/python' : process.env.PYTHONPATH,
        PATH: isLambda ? `/opt/python/bin:${process.env.PATH}` : process.env.PATH
    }
});
```

### Lambda Layer Structure

When deployed, the layer extracts to `/opt/`:

```
/opt/
├── python/
│   ├── bin/
│   │   └── python3          # Python 3.11 binary
│   ├── litellm/             # LiteLLM package
│   ├── boto3/               # AWS SDK
│   ├── openai/              # OpenAI client
│   └── [other dependencies]
```

## Dependencies

From `requirements.txt`:

```
litellm==1.78.7
boto3>=1.26.0
```

LiteLLM automatically pulls in:
- aiohttp
- httpx
- openai
- pydantic
- tiktoken
- And ~40 other transitive dependencies

## Troubleshooting

### Python Not Found Error

If you see `spawn /opt/python/bin/python3 ENOENT`:

1. Verify the layer is attached to the function in AWS Console
2. Check the layer ARN in CloudFormation outputs
3. Rebuild the layer: `./build-layer.sh`

### Module Import Errors

If Python can't find `litellm` or `boto3`:

1. Check `PYTHONPATH` is set to `/opt/python`
2. Verify the layer structure: `ls -la python/litellm`
3. Ensure Docker build completed successfully

### Layer Size Too Large

If the layer exceeds 250MB (compressed limit):

1. Review `build-layer.sh` cleanup section
2. Add more aggressive pruning:
   ```bash
   rm -rf python/*/tests
   rm -rf python/*/.so  # If safe to remove
   ```

## Local Development

For local development without the layer:

```bash
# Install Python dependencies locally
cd litellm-layer
pip install -r requirements.txt
```

The code will use `python3` from your system PATH instead of `/opt/python/bin/python3`.

## Version History

- **v1.0** - Initial layer with LiteLLM 1.78.7 and Python 3.11
