# Backend API Tests

This directory contains backend API tests that directly call API endpoints to validate their functionality.

**⚠️ IMPORTANT**: Please see [TESTING_SETUP.md](./TESTING_SETUP.md) for comprehensive setup instructions and current known issues.

## Overview

The tests are designed to make real HTTP requests to running services and validate:
- API endpoint functionality
- Request/response schemas
- Authentication and authorization
- Error handling

## Files

- `test_chat_endpoint.py` - Backend test for the chat endpoint API
- `config.py` - Configuration settings for tests
- `__init__.py` - Python package initialization

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
pip install pytest requests
```

2. Start the serverless offline service:
```bash
cd ../amplify-lambda
serverless offline --httpPort 3016 --stage dev --lambdaPort 3002
```

## Running Tests

### Run individual test:
```bash
python3 tests/test_chat_endpoint.py
```

### Run with pytest:
```bash
pytest tests/test_chat_endpoint.py -v
```

## Test Structure

Each test class follows the pattern:
- `test_<endpoint>_valid_request()` - Tests successful request
- `test_<endpoint>_invalid_schema()` - Tests schema validation
- `test_<endpoint>_unauthorized()` - Tests authentication

## Configuration

Tests use these environment variables:
- `API_BASE_URL` - Base URL for API calls (default: http://localhost:3016)
- `TEST_API_KEY` - API key for authenticated requests

## Expected Results

The tests validate:
- Correct HTTP status codes
- Response JSON structure with `success` and `message` fields
- Proper error handling for invalid requests
- Authentication and authorization behavior

## Current Status

✅ **UPDATE**: Integration tests are now **FULLY FUNCTIONAL** after resolving the serverless-offline tty issue!

- ✅ **Integration Tests**: **WORKING** - TTY issue resolved with serverless-offline patch
- ✅ **All Endpoints**: Responding properly with actual API responses
- ⚠️ **Unit Tests**: Limited functionality due to complex decorator-based Lambda architecture
- 🎉 **Recommended**: Use `test_working_endpoints.py` for comprehensive testing

See [TESTING_SETUP.md](./TESTING_SETUP.md) for detailed information and troubleshooting.

## Notes

- Tests are designed for the chat endpoint API in amplify-lambda
- The endpoint path includes the stage prefix: `/dev/chat`
- Serverless offline must be running for tests to pass (when tty issue is resolved)
- Tests handle network errors and timeouts gracefully
- Port configuration has been standardized to use 3016 (team standard)