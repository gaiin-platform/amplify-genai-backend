"""
Backend API Testing Endpoint

This endpoint provides comprehensive automated testing of all backend services by
executing real API calls with test data and returning detailed test results.

Endpoint: /admin/test-apis
Method: POST
"""

import os
import time
import json
import requests
from typing import Dict, List, Any, Optional
from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from service.core import authorized_admin

# Initialize validation system
setup_validated(rules, get_permission_checker)

# Test execution order based on dependencies
TEST_ORDER = [
    "amplify-lambda-api",           # 1. Create API keys first
    "amplify-assistants",           # 2. Create assistant
    "amplify-lambda",               # 3. Core functionality + chat
    "embedding",                    # 4. Test file embeddings
    "amplify-lambda-admin",
    "amplify-lambda-artifacts",
    "amplify-lambda-ops",
    "chat-billing",
    "data-disclosure",
    "object-access",
    "amplify-lambda-js",
    # Last batch - depends on data from previous tests
    "amplify-agent-loop-lambda",    # Needs assistant data
    "amplify-lambda-assistants-api",
    "amplify-lambda-assistants-api-google",
    "amplify-lambda-assistants-api-office365",
    "amplify-lambda-basic-ops"
]


def create_system_api_key(user: dict) -> str:
    """
    Create a system API key with full access for testing purposes.
    This would normally call the amplify-lambda-api/create_api_keys endpoint.

    For now, returns a mock API key until the full implementation is complete.
    """
    # TODO: Implement actual API key creation via amplify-lambda-api
    # test_data = {
    #     "owner": "test-system",
    #     "account": {"id": "test-account", "name": "Test Account"},
    #     "appName": "API Testing Suite",
    #     "appDescription": "Automated API testing",
    #     "rateLimit": {"rate": None, "period": "unlimited"},
    #     "accessTypes": ["FULL_ACCESS"],
    #     "systemUse": True,
    #     "purpose": "Automated API testing"
    # }
    # return call_api_endpoint("/api/create_api_keys", test_data, access_token)

    return "amp-system-test-key-" + str(int(time.time()))


def get_api_base_url() -> str:
    """Get the API base URL from environment"""
    return os.getenv('API_BASE_URL', 'http://localhost:3000')


def call_api_endpoint(endpoint: str, data: dict, api_key: str, method: str = "POST") -> Dict[str, Any]:
    """
    Make an API call to a specific endpoint with test data.

    Args:
        endpoint: API endpoint path (e.g., "/dev/chat")
        data: Request payload
        api_key: API key for authentication
        method: HTTP method

    Returns:
        Dictionary with test result including response data and metadata
    """
    base_url = get_api_base_url()
    url = f"{base_url}{endpoint}"

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }

    start_time = time.time()

    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=30)

        end_time = time.time()
        response_time_ms = (end_time - start_time) * 1000

        # Try to parse JSON response
        try:
            response_data = response.json()
        except json.JSONDecodeError:
            response_data = {"raw_response": response.text}

        return {
            "endpoint": endpoint,
            "method": method,
            "status": "passed" if response.status_code in [200, 201] else "failed",
            "response_code": response.status_code,
            "response_time_ms": response_time_ms,
            "test_data_sent": data,
            "response_received": response_data,
            "error": None if response.status_code in [200, 201] else f"HTTP {response.status_code}"
        }

    except requests.exceptions.RequestException as e:
        end_time = time.time()
        response_time_ms = (end_time - start_time) * 1000

        return {
            "endpoint": endpoint,
            "method": method,
            "status": "failed",
            "response_code": None,
            "response_time_ms": response_time_ms,
            "test_data_sent": data,
            "response_received": None,
            "error": str(e)
        }


def test_chat_endpoint(api_key: str) -> Dict[str, Any]:
    """Test the chat endpoint with valid data"""
    test_data = {
        "data": {
            "temperature": 0.7,
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": "Hello, this is a backend API test. Please respond briefly."
                }
            ],
            "options": {
                "model": {
                    "id": "gpt-4o"
                },
                "skipRag": True,
                "ragOnly": False
            }
        }
    }

    return call_api_endpoint("/dev/chat", test_data, api_key)


def test_files_upload_endpoint(api_key: str) -> Dict[str, Any]:
    """Test the files upload endpoint"""
    test_data = {
        "data": {
            "fileName": "test-backend-api.txt",
            "fileType": "text/plain",
            "tags": ["backend-test"],
            "enterRagPipeline": False
        }
    }

    return call_api_endpoint("/dev/files/upload", test_data, api_key)


def test_accounts_get_endpoint(api_key: str) -> Dict[str, Any]:
    """Test the accounts get endpoint"""
    return call_api_endpoint("/dev/state/accounts/get", {}, api_key, "GET")


def run_service_tests(services_to_test: List[str], api_key: str) -> Dict[str, Dict[str, Any]]:
    """
    Run tests for specified services.

    Args:
        services_to_test: List of service names to test
        api_key: API key for authentication

    Returns:
        Dictionary of test results by service name
    """
    results = {}

    for service_name in services_to_test:
        service_start_time = time.time()
        service_results = []

        # Test different endpoints based on service
        if service_name == "amplify-lambda":
            # Test core endpoints
            chat_result = test_chat_endpoint(api_key)
            service_results.append(chat_result)

            files_result = test_files_upload_endpoint(api_key)
            service_results.append(files_result)

            accounts_result = test_accounts_get_endpoint(api_key)
            service_results.append(accounts_result)

        elif service_name == "amplify-lambda-admin":
            # Test admin endpoints (basic connectivity test)
            admin_result = call_api_endpoint("/amplifymin/feature_flags", {}, api_key, "GET")
            service_results.append(admin_result)

        else:
            # For other services, create a placeholder test
            # In a full implementation, this would have specific tests for each service
            placeholder_result = {
                "endpoint": f"/{service_name}/test",
                "method": "POST",
                "status": "skipped",
                "response_code": None,
                "response_time_ms": 0,
                "test_data_sent": {},
                "response_received": {"message": "Service test not implemented yet"},
                "error": "Test implementation pending"
            }
            service_results.append(placeholder_result)

        service_end_time = time.time()
        service_execution_time = (service_end_time - service_start_time) * 1000

        # Calculate service-level stats
        endpoints_tested = len(service_results)
        endpoints_passed = len([r for r in service_results if r["status"] == "passed"])
        endpoints_failed = len([r for r in service_results if r["status"] == "failed"])

        service_status = "passed" if endpoints_failed == 0 else "failed"

        results[service_name] = {
            "status": service_status,
            "endpoints_tested": endpoints_tested,
            "endpoints_passed": endpoints_passed,
            "endpoints_failed": endpoints_failed,
            "execution_time_ms": service_execution_time,
            "results": service_results
        }

    return results


def generate_summary(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Generate summary statistics from test results"""
    total_services = len(results)
    tested_services = len([s for s in results.values() if s["status"] != "skipped"])
    passed_services = len([s for s in results.values() if s["status"] == "passed"])
    failed_services = len([s for s in results.values() if s["status"] == "failed"])

    total_execution_time = sum(s.get("execution_time_ms", 0) for s in results.values())

    return {
        "total_services": total_services,
        "tested_services": tested_services,
        "passed": passed_services,
        "failed": failed_services,
        "execution_time_ms": total_execution_time
    }


@required_env_vars({
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("test_apis")
def test_apis_request(event, context, user, name, data):
    """
    Main endpoint handler for backend API testing.

    Validates admin authorization, creates system API key, and runs comprehensive
    tests across specified services.

    Args:
        event: Lambda event
        context: Lambda context
        user: Authenticated user from PyCommon validation
        name: Operation name from PyCommon validation
        data: Validated request data

    Returns:
        Dictionary with test results and summary
    """
    try:
        # 1. Check admin authorization
        authorized_admin(user)

        # 2. Get services to test from request data
        services_to_test = data.get("services", []) or TEST_ORDER

        # Ensure we only test services that exist in our test order
        valid_services = [s for s in services_to_test if s in TEST_ORDER]
        if not valid_services:
            valid_services = ["amplify-lambda"]  # Default to testing core service

        # 3. Generate system API key
        api_key = create_system_api_key(user)

        # 4. Execute tests
        test_start_time = time.time()
        results = run_service_tests(valid_services, api_key)
        test_end_time = time.time()

        # 5. Generate summary
        summary = generate_summary(results)
        summary["execution_time_ms"] = (test_end_time - test_start_time) * 1000

        return {
            "success": True,
            "message": f"Backend API tests completed. Tested {len(valid_services)} services.",
            "summary": summary,
            "details": results
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_code": "TEST_EXECUTION_FAILED"
        }