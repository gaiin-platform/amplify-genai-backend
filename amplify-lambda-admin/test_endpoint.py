#!/usr/bin/env python3
"""
Quick test script to test the new /admin/test-apis endpoint
"""

import requests
import json

def test_admin_test_apis_endpoint():
    """Test the new admin test-apis endpoint"""

    url = "http://localhost:3016/dev/admin/test-apis"

    # Test data - requesting to test specific services
    test_data = {
        "services": ["amplify-lambda"]
    }

    headers = {
        'Content-Type': 'application/json'
    }

    print("=" * 60)
    print("Testing /admin/test-apis endpoint")
    print("=" * 60)
    print(f"URL: {url}")
    print(f"Data: {json.dumps(test_data, indent=2)}")
    print()

    try:
        response = requests.post(url, json=test_data, headers=headers, timeout=60)

        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print()

        # Try to parse JSON response
        try:
            response_data = response.json()
            print("Response JSON:")
            print(json.dumps(response_data, indent=2))
        except json.JSONDecodeError:
            print("Response Text:")
            print(response.text)

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")

    print("=" * 60)

if __name__ == "__main__":
    test_admin_test_apis_endpoint()