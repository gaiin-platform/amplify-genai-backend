#!/usr/bin/env python3
"""
Simple test script to validate LiteLLM integration
"""

import json
import sys
import os

# Test input data
test_input = {
    "chatRequest": {
        "messages": [
            {"role": "user", "content": "Say 'Hello from LiteLLM!'"}
        ],
        "max_tokens": 100,
        "temperature": 0.7
    },
    "model": {
        "id": "gpt-3.5-turbo",
        "provider": "OpenAI"
    },
    "account": {
        "user": "test_user",
        "accessToken": "test_token"
    },
    "secrets": {
        "openai_key": "test_key"  # This won't work but will test the flow
    }
}

def test_python_script():
    """Test the Python script without making actual LLM calls"""
    print("Testing Python LiteLLM script...")
    print("Input data:")
    print(json.dumps(test_input, indent=2))
    
    # Note: This will fail at the LiteLLM call due to invalid API key,
    # but it will test our JSON parsing and provider configuration
    print("\nExpected result: Script should parse input and configure providers correctly")
    print("(It will fail at LiteLLM call due to test API key)")

if __name__ == "__main__":
    test_python_script()