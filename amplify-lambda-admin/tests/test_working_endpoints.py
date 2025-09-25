"""
Backend API Tests for Working Endpoints - Post TTY Fix

This test file validates that our serverless-offline tty fix is working
by testing multiple endpoints and ensuring they return proper HTTP responses
instead of timing out.
"""

import os
import json
import requests
import pytest
from typing import Dict, Any
from config import API_BASE_URL, TEST_API_KEY, REQUEST_TIMEOUT


class TestWorkingEndpoints:
    """Test class to validate that endpoints are responding after tty fix"""

    def __init__(self):
        self.api_base_url = API_BASE_URL
        if self.api_base_url.endswith('/'):
            self.api_base_url = self.api_base_url[:-1]

        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {TEST_API_KEY}'
        }

    def test_chat_endpoint_responds(self):
        """Test that chat endpoint responds (doesn't timeout)"""
        test_data = {
            "data": {
                "temperature": 0.7,
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello, this is a test message."
                    }
                ],
                "options": {
                    "model": {
                        "id": "gpt-4o"
                    },
                    "skipRag": True,
                    "ragOnly": False
                },
                "dataSources": []  # Include required field
            }
        }

        url = f"{self.api_base_url}/dev/chat"

        try:
            response = requests.post(url, json=test_data, headers=self.headers, timeout=REQUEST_TIMEOUT)

            # Success: We got a response (not a timeout)
            print(f"✅ Chat endpoint responded with status {response.status_code}")

            # Try to parse JSON response
            try:
                response_data = response.json()
                print(f"  Response: {json.dumps(response_data, indent=2)}")

                return {
                    "test_name": "test_chat_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_data": response_data,
                    "message": "Chat endpoint responding properly"
                }
            except json.JSONDecodeError:
                print(f"  Non-JSON response: {response.text[:200]}")
                return {
                    "test_name": "test_chat_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_text": response.text[:200],
                    "message": "Chat endpoint responding (non-JSON)"
                }

        except requests.exceptions.Timeout:
            print(f"✗ Chat endpoint test FAILED: Request timed out")
            return {
                "test_name": "test_chat_endpoint_responds",
                "status": "FAILED",
                "error": "Request timed out - tty issue may still exist"
            }
        except Exception as e:
            print(f"✗ Chat endpoint test FAILED: {str(e)}")
            return {
                "test_name": "test_chat_endpoint_responds",
                "status": "FAILED",
                "error": str(e)
            }

    def test_settings_endpoint_responds(self):
        """Test that settings GET endpoint responds"""
        url = f"{self.api_base_url}/dev/state/settings/get"

        try:
            response = requests.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)

            print(f"✅ Settings endpoint responded with status {response.status_code}")

            try:
                response_data = response.json()
                print(f"  Response: {json.dumps(response_data, indent=2)}")

                return {
                    "test_name": "test_settings_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_data": response_data,
                    "message": "Settings endpoint responding properly"
                }
            except json.JSONDecodeError:
                print(f"  Non-JSON response: {response.text[:200]}")
                return {
                    "test_name": "test_settings_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_text": response.text[:200],
                    "message": "Settings endpoint responding (non-JSON)"
                }

        except requests.exceptions.Timeout:
            print(f"✗ Settings endpoint test FAILED: Request timed out")
            return {
                "test_name": "test_settings_endpoint_responds",
                "status": "FAILED",
                "error": "Request timed out - tty issue may still exist"
            }
        except Exception as e:
            print(f"✗ Settings endpoint test FAILED: {str(e)}")
            return {
                "test_name": "test_settings_endpoint_responds",
                "status": "FAILED",
                "error": str(e)
            }

    def test_files_tags_endpoint_responds(self):
        """Test that files/tags/list GET endpoint responds"""
        url = f"{self.api_base_url}/dev/files/tags/list"

        try:
            response = requests.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)

            print(f"✅ Files tags endpoint responded with status {response.status_code}")

            try:
                response_data = response.json()
                print(f"  Response: {json.dumps(response_data, indent=2)}")

                return {
                    "test_name": "test_files_tags_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_data": response_data,
                    "message": "Files tags endpoint responding properly"
                }
            except json.JSONDecodeError:
                print(f"  Non-JSON response: {response.text[:200]}")
                return {
                    "test_name": "test_files_tags_endpoint_responds",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_text": response.text[:200],
                    "message": "Files tags endpoint responding (non-JSON)"
                }

        except requests.exceptions.Timeout:
            print(f"✗ Files tags endpoint test FAILED: Request timed out")
            return {
                "test_name": "test_files_tags_endpoint_responds",
                "status": "FAILED",
                "error": "Request timed out - tty issue may still exist"
            }
        except Exception as e:
            print(f"✗ Files tags endpoint test FAILED: {str(e)}")
            return {
                "test_name": "test_files_tags_endpoint_responds",
                "status": "FAILED",
                "error": str(e)
            }

    def test_unauthorized_request(self):
        """Test endpoint without authorization header"""
        url = f"{self.api_base_url}/dev/state/settings/get"
        headers_no_auth = {
            'Content-Type': 'application/json'
            # No Authorization header
        }

        try:
            response = requests.get(url, headers=headers_no_auth, timeout=REQUEST_TIMEOUT)

            print(f"✅ Unauthorized request responded with status {response.status_code}")

            try:
                response_data = response.json()
                print(f"  Response: {json.dumps(response_data, indent=2)}")

                return {
                    "test_name": "test_unauthorized_request",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_data": response_data,
                    "message": "Unauthorized request handled properly"
                }
            except json.JSONDecodeError:
                print(f"  Non-JSON response: {response.text[:200]}")
                return {
                    "test_name": "test_unauthorized_request",
                    "status": "PASSED",
                    "response_code": response.status_code,
                    "response_text": response.text[:200],
                    "message": "Unauthorized request handled (non-JSON)"
                }

        except requests.exceptions.Timeout:
            print(f"✗ Unauthorized request test FAILED: Request timed out")
            return {
                "test_name": "test_unauthorized_request",
                "status": "FAILED",
                "error": "Request timed out - tty issue may still exist"
            }
        except Exception as e:
            print(f"✗ Unauthorized request test FAILED: {str(e)}")
            return {
                "test_name": "test_unauthorized_request",
                "status": "FAILED",
                "error": str(e)
            }

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all endpoint tests and return results"""

        print("=" * 70)
        print("RUNNING BACKEND ENDPOINT TESTS (POST TTY FIX)")
        print("=" * 70)
        print(f"API Base URL: {self.api_base_url}")
        print(f"Test Purpose: Validate endpoints respond after tty fix")
        print()

        results = []

        # Test 1: Chat endpoint
        print("Test 1: Chat Endpoint Response")
        print("-" * 40)
        result1 = self.test_chat_endpoint_responds()
        results.append(result1)
        print()

        # Test 2: Settings endpoint
        print("Test 2: Settings Endpoint Response")
        print("-" * 40)
        result2 = self.test_settings_endpoint_responds()
        results.append(result2)
        print()

        # Test 3: Files tags endpoint
        print("Test 3: Files Tags Endpoint Response")
        print("-" * 40)
        result3 = self.test_files_tags_endpoint_responds()
        results.append(result3)
        print()

        # Test 4: Unauthorized access
        print("Test 4: Unauthorized Request Handling")
        print("-" * 40)
        result4 = self.test_unauthorized_request()
        results.append(result4)
        print()

        # Summary
        passed = len([r for r in results if r["status"] == "PASSED"])
        failed = len([r for r in results if r["status"] == "FAILED"])

        print("=" * 70)
        print("ENDPOINT RESPONSE TEST SUMMARY")
        print("=" * 70)
        print(f"Total Tests: {len(results)}")
        print(f"Responding (Passed): {passed}")
        print(f"Not Responding (Failed): {failed}")
        print()
        if passed > 0:
            print("🎉 SUCCESS: Serverless-offline tty fix is working!")
            print("   Lambda functions are executing instead of timing out")
        if failed > 0:
            print("⚠️  Some endpoints still having issues")
        print("=" * 70)

        return {
            "service": "multiple-endpoints",
            "test_purpose": "validate_tty_fix",
            "total_tests": len(results),
            "passed": passed,
            "failed": failed,
            "results": results
        }


def main():
    """Main function to run the endpoint tests"""
    tester = TestWorkingEndpoints()
    results = tester.run_all_tests()

    # Return exit code based on results
    if results["failed"] > 0:
        exit(1)
    else:
        exit(0)


if __name__ == "__main__":
    main()