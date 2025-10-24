# Python LiteLLM Lambda Layer

This Lambda layer provides Python 3.11 runtime with LiteLLM and boto3 for the Node.js Lambda function.

## Overview

The `amplify-lambda-js` service is a **Node.js 22.x Lambda** that needs to spawn Python subprocesses to run LiteLM for unified LLM provider access. This layer provides:

- Python 3.11 binary at `/opt/python/bin/python3`
- LiteLLM 1.78.7 and all dependencies
- boto3 for AWS SDK access

## Build Instructions

### Prerequisites
- Docker installed and running
- Bash shell

### Building the Layer

```bash
cd litellm-layer
./build-layer.sh
```

This will:
1. Use Docker with the official Lambda Python 3.11 image
2. Install all Python dependencies from `requirements.txt`
3. Extract the Python 3.11 binary from the Lambda container
4. Clean up unnecessary files (tests, docs, `__pycache__`, etc.)
5. Produce a `python/` directory ready for Lambda deployment

### Layer Size

Expected layer size: ~117MB (uncompressed)

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
