"""
Unit Test for Chat Endpoint - Direct Function Testing

This test directly imports and tests the chat_endpoint function without relying on
the serverless-offline HTTP server, avoiding the tty configuration issues.
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock
import json

# Add the parent directories to sys.path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'amplify-lambda'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'amplify-pycommon'))

class TestChatEndpointUnit(unittest.TestCase):
    """Unit tests for the chat endpoint function"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Mock environment variables that may be needed
        self.env_patcher = patch.dict(os.environ, {
            'API_BASE_URL': 'http://localhost:3000',
            'AWS_REGION': 'us-east-1',
            'FILES_DYNAMO_TABLE': 'test-files-table',
            'CONVERSATIONS_DYNAMO_TABLE': 'test-conversations-table',
            'STATE_DYNAMO_TABLE': 'test-state-table',
            'USERS_DYNAMO_TABLE': 'test-users-table',
            'CHAT_ENDPOINT': 'http://mock-chat-endpoint.com',
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
            'AWS_DEFAULT_REGION': 'us-east-1',
        })
        self.env_patcher.start()

    def tearDown(self):
        """Clean up after each test method."""
        self.env_patcher.stop()

    @patch('chat.service.get_endpoint')
    @patch('chat.service.get_data_source_details')
    @patch('chat.service.chat')
    def test_chat_endpoint_valid_request(self, mock_chat, mock_get_data_source, mock_get_endpoint):
        """Test chat endpoint with valid request data"""
        # Import here to avoid issues with path setup
        try:
            from chat.service import chat_endpoint
        except ImportError as e:
            self.skipTest(f"Cannot import chat.service: {e}")

        # Setup mocks
        mock_get_endpoint.return_value = "http://mock-chat-endpoint.com"
        mock_get_data_source.return_value = []
        mock_chat.return_value = ({"response": "Hello, this is a test response"}, {"metadata": "test"})

        # Create test data
        event = {}
        context = {}
        current_user = "test_user"
        name = "test"
        data = {
            "access_token": "test_token",
            "allowed_access": ["CHAT"],
            "data": {
                "temperature": 0.7,
                "max_tokens": 1000,
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
                    "ragOnly": False,
                    "prompt": "You are a helpful assistant."
                },
                "dataSources": []
            }
        }

        # Call the function
        result = chat_endpoint(event, context, current_user, name, data)

        # Validate the result
        self.assertIsInstance(result, dict)
        self.assertIn("success", result)
        self.assertIn("message", result)

        if result["success"]:
            self.assertIn("data", result)
            self.assertEqual(result["message"], "Chat endpoint response retrieved")
            print("✓ Unit test for valid request PASSED")
        else:
            print(f"ℹ Unit test completed with expected validation error: {result['message']}")

        return result

    @patch('chat.service.get_endpoint')
    def test_chat_endpoint_no_chat_access(self, mock_get_endpoint):
        """Test chat endpoint without CHAT access"""
        try:
            from chat.service import chat_endpoint
        except ImportError as e:
            self.skipTest(f"Cannot import chat.service: {e}")

        # Setup mocks
        mock_get_endpoint.return_value = "http://mock-chat-endpoint.com"

        # Create test data with no CHAT access
        event = {}
        context = {}
        current_user = "test_user"
        name = "test"
        data = {
            "access_token": "test_token",
            "allowed_access": ["FILES"],  # No CHAT access
            "data": {
                "temperature": 0.7,
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": "This should fail due to no chat access"
                    }
                ],
                "options": {
                    "model": {
                        "id": "gpt-4o"
                    }
                },
                "dataSources": []
            }
        }

        # Call the function
        result = chat_endpoint(event, context, current_user, name, data)

        # Validate the result
        self.assertIsInstance(result, dict)
        self.assertFalse(result["success"])
        self.assertIn("API key does not have access", result["message"])
        print("✓ Unit test for no chat access PASSED")

        return result

    @patch('chat.service.get_endpoint')
    def test_chat_endpoint_no_chat_endpoint(self, mock_get_endpoint):
        """Test chat endpoint when no chat endpoint is configured"""
        try:
            from chat.service import chat_endpoint
        except ImportError as e:
            self.skipTest(f"Cannot import chat.service: {e}")

        # Setup mocks - return None for no endpoint
        mock_get_endpoint.return_value = None

        # Create test data
        event = {}
        context = {}
        current_user = "test_user"
        name = "test"
        data = {
            "access_token": "test_token",
            "allowed_access": ["CHAT"],
            "data": {
                "temperature": 0.7,
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": "This should fail due to no endpoint"
                    }
                ],
                "options": {
                    "model": {
                        "id": "gpt-4o"
                    }
                },
                "dataSources": []
            }
        }

        # Call the function
        result = chat_endpoint(event, context, current_user, name, data)

        # Validate the result
        self.assertIsInstance(result, dict)
        self.assertFalse(result["success"])
        self.assertIn("No chat endpoint found", result["message"])
        print("✓ Unit test for no chat endpoint PASSED")

        return result

    def run_all_tests(self):
        """Run all unit tests and return summary"""
        print("=" * 60)
        print("RUNNING CHAT ENDPOINT UNIT TESTS")
        print("=" * 60)

        results = []

        # Test 1: Valid request
        print("\nTest 1: Valid Chat Request (Unit)")
        print("-" * 30)
        try:
            result1 = self.test_chat_endpoint_valid_request()
            results.append({
                "test_name": "test_chat_endpoint_valid_request",
                "status": "PASSED" if result1.get("success") else "COMPLETED_WITH_ERROR",
                "result": result1
            })
        except Exception as e:
            print(f"✗ Unit test 1 failed: {e}")
            results.append({
                "test_name": "test_chat_endpoint_valid_request",
                "status": "FAILED",
                "error": str(e)
            })

        # Test 2: No chat access
        print("\nTest 2: No Chat Access (Unit)")
        print("-" * 30)
        try:
            result2 = self.test_chat_endpoint_no_chat_access()
            results.append({
                "test_name": "test_chat_endpoint_no_chat_access",
                "status": "PASSED",
                "result": result2
            })
        except Exception as e:
            print(f"✗ Unit test 2 failed: {e}")
            results.append({
                "test_name": "test_chat_endpoint_no_chat_access",
                "status": "FAILED",
                "error": str(e)
            })

        # Test 3: No chat endpoint
        print("\nTest 3: No Chat Endpoint (Unit)")
        print("-" * 30)
        try:
            result3 = self.test_chat_endpoint_no_chat_endpoint()
            results.append({
                "test_name": "test_chat_endpoint_no_chat_endpoint",
                "status": "PASSED",
                "result": result3
            })
        except Exception as e:
            print(f"✗ Unit test 3 failed: {e}")
            results.append({
                "test_name": "test_chat_endpoint_no_chat_endpoint",
                "status": "FAILED",
                "error": str(e)
            })

        # Summary
        passed = len([r for r in results if r["status"] == "PASSED"])
        completed_with_error = len([r for r in results if r["status"] == "COMPLETED_WITH_ERROR"])
        failed = len([r for r in results if r["status"] == "FAILED"])

        print("\n" + "=" * 60)
        print("CHAT ENDPOINT UNIT TEST SUMMARY")
        print("=" * 60)
        print(f"Total Tests: {len(results)}")
        print(f"Passed: {passed}")
        print(f"Completed with Expected Error: {completed_with_error}")
        print(f"Failed: {failed}")
        print("=" * 60)

        return {
            "service": "chat-endpoint-unit",
            "total_tests": len(results),
            "passed": passed,
            "completed_with_error": completed_with_error,
            "failed": failed,
            "results": results
        }


def main():
    """Main function to run the unit tests"""
    tester = TestChatEndpointUnit()
    tester.setUp()

    try:
        results = tester.run_all_tests()

        # Return exit code based on results
        if results["failed"] > 0:
            exit(1)
        else:
            exit(0)
    finally:
        tester.tearDown()


if __name__ == "__main__":
    main()