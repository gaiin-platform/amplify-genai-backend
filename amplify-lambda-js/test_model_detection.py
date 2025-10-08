#!/usr/bin/env python3
"""
Test model detection and reasoning config generation
"""

import sys
import os
sys.path.append('common')

# Import functions from our script
def is_bedrock_model(model_id: str) -> bool:
    """Check if model is Bedrock-based"""
    bedrock_patterns = ["anthropic", "claude", "amazon", "titan", "ai21", "cohere", "meta", "deepseek"]
    return any(provider in model_id for provider in bedrock_patterns)

def setup_reasoning_config(model: dict, chat_request: dict, model_str: str) -> dict:
    """Setup reasoning configuration based on model and provider"""
    if not model.get("supportsReasoning"):
        return {}
        
    # Get reasoning level from options
    reasoning_level = chat_request.get("options", {}).get("reasoningLevel", "low")
    max_tokens = chat_request.get("max_tokens", 1000)
    
    # Budget tokens calculation
    budget_tokens = 1024
    if reasoning_level == "medium":
        budget_tokens = 2048
    elif reasoning_level == "high":
        budget_tokens = 4096
        
    if budget_tokens > max_tokens:
        budget_tokens = max(max_tokens // 2, 1024)
    
    if "azure/" in model_str or "gpt" in model["id"]:
        return {"reasoning": {"effort": reasoning_level, "summary": "auto"}}
            
    elif "bedrock/" in model_str:
        # Bedrock reasoning format - only for Claude 3+ models that support it
        model_id = model["id"].lower()
        if "claude-3" in model_id or "claude-sonnet" in model_id or "claude-opus" in model_id:
            return {
                "additional_kwargs": {
                    "reasoning_config": {
                        "type": "enabled", 
                        "budget_tokens": budget_tokens
                    }
                }
            }
        return {}
        
    elif "gemini/" in model_str:
        return {
            "extra_body": {
                "google": {
                    "thinking_config": {
                        "thinking_budget": budget_tokens,
                        "include_thoughts": True
                    }
                }
            }
        }
        
    return {}

# Test cases
test_cases = [
    {
        "name": "Bedrock Claude 3 (should get reasoning config)",
        "model": {"id": "anthropic.claude-3-sonnet-20240229-v1:0", "supportsReasoning": True},
        "model_str": "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
        "chat_request": {"options": {}, "max_tokens": 1000}
    },
    {
        "name": "Bedrock Titan (should NOT get reasoning config)", 
        "model": {"id": "amazon.titan-text-express-v1", "supportsReasoning": True},
        "model_str": "bedrock/amazon.titan-text-express-v1",
        "chat_request": {"options": {}, "max_tokens": 1000}
    },
    {
        "name": "OpenAI GPT-4 (should get reasoning config)",
        "model": {"id": "gpt-4o", "supportsReasoning": True},
        "model_str": "gpt-4o",
        "chat_request": {"options": {}, "max_tokens": 1000}
    }
]

print("=== Testing Model Detection and Reasoning Config ===\n")

for test in test_cases:
    print(f"Test: {test['name']}")
    print(f"Model ID: {test['model']['id']}")
    print(f"Model String: {test['model_str']}")
    print(f"Is Bedrock: {is_bedrock_model(test['model']['id'])}")
    
    config = setup_reasoning_config(test['model'], test['chat_request'], test['model_str'])
    print(f"Reasoning Config: {config}")
    print("---")