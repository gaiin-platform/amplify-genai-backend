#!/usr/bin/env python3
"""
Test script to verify the persistent Python LiteLLM server works correctly
"""

import json
import subprocess
import sys
import os
from pathlib import Path

def test_persistent_server():
    """Test the persistent Python server with multiple requests"""
    
    # Path to the Python script
    script_path = Path(__file__).parent / "common" / "amplify_litellm.py"
    
    # Start the server process
    print("Starting persistent Python LiteLLM server...")
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    # Test request data
    test_request = {
        "requestId": "test_123",
        "chatRequest": {
            "messages": [
                {"role": "user", "content": "Hello, world!"}
            ],
            "max_tokens": 50,
            "temperature": 0.7
        },
        "model": {
            "id": "gpt-3.5-turbo",
            "supportsReasoning": False
        },
        "account": {"id": "test"},
        "secrets": {
            "openai_key": "test-key-placeholder"
        },
        "dataSources": []
    }
    
    try:
        # Send the test request
        print("Sending test request...")
        request_line = json.dumps(test_request) + "\n"
        process.stdin.write(request_line)
        process.stdin.flush()
        
        # Read responses
        print("Reading responses...")
        while True:
            line = process.stdout.readline()
            if not line:
                break
            
            try:
                response = json.loads(line.strip())
                print(f"Response: {response}")
                
                if response.get("type") == "end":
                    print("Request completed successfully!")
                    break
                elif response.get("type") == "error":
                    print(f"Request failed: {response.get('data', {}).get('message')}")
                    break
                    
            except json.JSONDecodeError:
                print(f"Non-JSON output: {line.strip()}")
                
        # Test a second request to verify persistence
        print("\nSending second test request...")
        test_request["requestId"] = "test_456"
        test_request["chatRequest"]["messages"][0]["content"] = "Second request"
        
        request_line = json.dumps(test_request) + "\n"
        process.stdin.write(request_line)
        process.stdin.flush()
        
        # Read second response
        while True:
            line = process.stdout.readline()
            if not line:
                break
            
            try:
                response = json.loads(line.strip())
                print(f"Response: {response}")
                
                if response.get("type") == "end":
                    print("Second request completed successfully!")
                    break
                elif response.get("type") == "error":
                    print(f"Second request failed: {response.get('data', {}).get('message')}")
                    break
                    
            except json.JSONDecodeError:
                print(f"Non-JSON output: {line.strip()}")
        
    finally:
        # Clean up
        print("\nShutting down server...")
        process.stdin.close()
        process.terminate()
        
        # Get any stderr output
        stderr_output = process.stderr.read()
        if stderr_output:
            print(f"Server stderr:\n{stderr_output}")

if __name__ == "__main__":
    test_persistent_server()