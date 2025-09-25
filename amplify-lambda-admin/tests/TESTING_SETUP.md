# Backend API Testing Setup Guide

This guide explains how to set up and run backend tests for the Amplify GenAI system.

## Overview

The testing setup includes:
- **Integration Tests**: Direct HTTP calls to API endpoints (via serverless-offline)
- **Unit Tests**: Direct function testing (bypassing HTTP layer)
- **Configuration**: Centralized test configuration

## Test Structure

```
amplify-lambda-admin/tests/
├── README.md                 # Original test documentation
├── TESTING_SETUP.md         # This setup guide
├── config.py                # Centralized test configuration
├── test_chat_endpoint.py    # Integration tests for chat endpoint
├── test_chat_unit.py        # Unit tests for chat endpoint (partial)
├── test_working_endpoints.py # ✅ WORKING endpoint tests (post-fix)
└── __init__.py             # Python package initialization
```

## Prerequisites

### Required Software
- Python 3.11+
- Node.js 18+
- npm or yarn
- Serverless Framework CLI

### Python Dependencies
```bash
pip install pytest requests unittest-mock
```

### Node.js Dependencies
```bash
cd amplify-genai-backend
npm install
```

## Configuration

### Environment Variables

Set these environment variables or add them to your shell profile:

```bash
# API Configuration
export API_BASE_URL="http://localhost:3016"
export TEST_API_KEY=""

# AWS Configuration (for local testing)
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"

# DynamoDB Table Names (for local testing)
export FILES_DYNAMO_TABLE="test-files-table"
export CONVERSATIONS_DYNAMO_TABLE="test-conversations-table"
export STATE_DYNAMO_TABLE="test-state-table"
export USERS_DYNAMO_TABLE="test-users-table"
```

### Test Configuration File

The `config.py` file centralizes test configuration:

```python
# API Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:3016')
TEST_API_KEY = os.getenv('TEST_API_KEY', '')

# Test timeouts
REQUEST_TIMEOUT = 30

# Expected response codes for different scenarios
EXPECTED_SUCCESS_CODES = [200]
EXPECTED_AUTH_ERROR_CODES = [401, 403]
EXPECTED_VALIDATION_ERROR_CODES = [400]
```

## Running Tests

### Method 1: Integration Tests (with serverless-offline)

**⚠️ Known Issue**: The current serverless-offline setup has a Python `/dev/tty` access issue that prevents Lambda functions from executing properly in non-interactive environments.

#### Starting the Service

1. Navigate to the amplify-lambda directory:
```bash
cd amplify-genai-backend/amplify-lambda
```

2. Start serverless offline (this will show the tty error):
```bash
npx serverless offline --stage dev --httpPort 3016 --lambdaPort 3002
```

#### Running Integration Tests

```bash
cd amplify-genai-backend/amplify-lambda-admin
python3 tests/test_chat_endpoint.py
```

**Expected Result**: Tests will timeout due to the serverless-offline tty issue.

### Method 2: pytest Integration Tests

```bash
cd amplify-genai-backend/amplify-lambda-admin
pytest tests/test_chat_endpoint.py -v
```

### Method 3: Unit Tests

Unit tests bypass the serverless-offline HTTP layer and test functions directly:

```bash
cd amplify-genai-backend/amplify-lambda-admin
python3 tests/test_chat_unit.py
```

**Note**: Unit tests are currently limited due to the complex decorator-based architecture of the Lambda functions.

### Method 4: Working Endpoint Tests (✅ RECOMMENDED)

**NEW**: Comprehensive tests that validate the TTY fix is working:

```bash
cd amplify-genai-backend/amplify-lambda-admin
python3 tests/test_working_endpoints.py
```

**Results**: All endpoints now respond properly with actual API responses instead of timing out.

## Known Issues and Limitations

### 1. Serverless-Offline TTY Issue

**✅ RESOLVED**: The serverless-offline Python runner `/dev/tty` access issue has been fixed.

**Original Problem**: The serverless-offline Python runner tried to access `/dev/tty` which failed in non-interactive environments.

**Solution Applied**: Patched `node_modules/serverless-offline/src/lambda/handler-runner/python-runner/invoke.py`
to wrap the `/dev/tty` access in a try-catch block.

**Fix Details**:
```python
# Before (line 91):
sys.stdin = open('/dev/tty')

# After (lines 91-95):
try:
    sys.stdin = open('/dev/tty')
except OSError:
    # /dev/tty not available, continue without TTY replacement
    pass
```

**Status**: ✅ **WORKING** - All Lambda functions now execute successfully.

### 2. Complex Lambda Function Architecture

**Problem**: Lambda functions use complex decorator-based validation and authorization systems that are difficult to mock for unit testing.

**Impact**: Direct unit testing of Lambda functions requires extensive mocking of the entire validation/authorization framework.

**Current Status**: Partial unit tests created but require significant additional work.

### 3. Environment Variable Dependencies

**Problem**: Lambda functions require numerous AWS-specific environment variables that must be mocked for local testing.

**Solution**: Extensive environment variable mocking in test setup.

## Recommended Testing Approach

Given the current limitations, here's the recommended testing strategy:

### 1. Configuration Testing
Test that configuration files and environment variables are properly set up:

```bash
cd amplify-genai-backend/amplify-lambda-admin
python3 -c "from tests.config import API_BASE_URL, TEST_API_KEY; print(f'API: {API_BASE_URL}, Key: {TEST_API_KEY[:20]}...')"
```

### 2. Service Availability Testing
Test that the serverless service starts (even if functions don't work):

```bash
cd amplify-genai-backend/amplify-lambda
npx serverless offline --stage dev --httpPort 3016 --lambdaPort 3002
# Check http://localhost:3016 in browser - should show endpoint list
```

### 3. Manual API Testing
Use curl or Postman to test API endpoints once the tty issue is resolved:

```bash
curl -X POST http://localhost:3016/dev/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer amp-" \
  -d '{"data": {"temperature": 0.7, "max_tokens": 100, "messages": [{"role": "user", "content": "test"}], "options": {"model": {"id": "gpt-4o"}}}}'
```

## Future Improvements

### 1. Docker-based Testing
Implement Docker containers for the entire testing environment to avoid local serverless-offline issues.

### 2. Mock Service Layer
Create a mock HTTP service that simulates the Lambda responses without requiring the actual Lambda runtime.

### 3. Enhanced Unit Testing
Develop comprehensive mocking for the validation/authorization framework to enable proper unit testing.

### 4. CI/CD Integration
Set up automated testing pipelines that can handle the current limitations or use alternative testing approaches.

## Test Results Summary

| Test Type | Status | Issues |
|-----------|--------|--------|
| Integration Tests | ✅ **WORKING** | **TTY issue RESOLVED** |
| Unit Tests | ⚠️ Partial | Complex decorator architecture |
| Configuration | ✅ Working | Port mismatch fixed |
| Service Startup | ✅ **WORKING** | **All endpoints functional** |
| TTY Fix | ✅ **IMPLEMENTED** | **Serverless-offline patched** |

## Troubleshooting

### Port Configuration Mismatch
**Fixed**: Updated `config.py` to use port 3016 (matching team standard) instead of 3000.

### Missing Environment Variables
**Fixed**: Added comprehensive environment variable mocking in test setup.

### Import Path Issues
**Fixed**: Added proper sys.path configuration for importing Lambda functions.

### TTY Access Error
**✅ RESOLVED**: Fixed by patching serverless-offline Python runner to handle missing `/dev/tty` gracefully.

**Fix Applied**: Modified `node_modules/serverless-offline/src/lambda/handler-runner/python-runner/invoke.py`
to wrap the `/dev/tty` access in a try-catch block, preventing crashes when the terminal device is not available.

**Result**: All Lambda functions now execute successfully instead of timing out.

## Support

For questions about this testing setup:
1. Check the existing test files for examples
2. Review the serverless.yml configuration for endpoint definitions
3. Consider the known limitations when planning test strategies
4. Use manual testing approaches until automated testing is fully functional