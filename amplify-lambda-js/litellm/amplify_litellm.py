#!/usr/bin/env python3
"""
LiteLLM Integration for Amplify Lambda JS
Replaces complex provider-specific implementations with unified LiteLLM interface
"""

import json
import sys
import os
import re
import traceback
import resource
from typing import Dict, Any, Iterator
import boto3
import litellm
from litellm import completion
import logging

# Set up logging with LOG_LEVEL from environment
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr  # Log to stderr so it doesn't interfere with JSON output to stdout
)
logger = logging.getLogger('amplify_litellm')

# Global memory tracking
initial_memory = None


def output_message(message_data: Dict[str, Any], request_id: str = None) -> None:
    """Output structured message to stdout for Node.js consumption"""
    if request_id:
        message_data["requestId"] = request_id
    print(json.dumps(message_data), flush=True)


def is_openai_model(model_id: str) -> bool:
    """Check if model is OpenAI-based"""
    return model_id and ("gpt" in model_id or re.match(r'^o\d', model_id))


def is_bedrock_model(model_id: str) -> bool:
    """Check if model is Bedrock-based"""
    bedrock_patterns = ["anthropic", "claude", "amazon", "titan", "ai21", "cohere", "meta", "deepseek"]
    return any(provider in model_id for provider in bedrock_patterns)


def is_gemini_model(model_id: str) -> bool:
    """Check if model is Gemini-based"""
    return model_id and "gemini" in model_id


def translate_model_name(model_id: str) -> str:
    """Translate model names to standard format"""
    translations = {
        "gpt-4-1106-Preview": "gpt-4-turbo",
        "gpt-35-turbo": "gpt-3.5-turbo"
    }
    return translations.get(model_id, model_id)


def configure_litellm(model: Dict[str, Any], secrets: Dict[str, Any]) -> tuple:
    """Configure LiteLLM for different providers and return (model_string, config_dict)
    
    Returns per-request configuration to support concurrent requests with different models
    """
    model_id = translate_model_name(model["id"])
    config = {}
    
    if is_openai_model(model_id):
        if "azure_config" in secrets and secrets["azure_config"]:
            # Azure OpenAI configuration - use per-request config
            azure_config = secrets["azure_config"]
            base_url = azure_config["url"].split("/openai")[0] if "/openai" in azure_config["url"] else azure_config["url"]
            
            # Extract version from URL if present
            version = "2024-02-15-preview"  # Default version
            if "?" in azure_config["url"]:
                query_params = azure_config["url"].split("?")[1]
                for param in query_params.split("&"):
                    if param.startswith("api-version="):
                        version = param.split("=")[1]
                        break
            
            # Use per-request configuration instead of global env vars
            config = {
                "api_key": azure_config["key"],
                "api_base": base_url,
                "api_version": version
            }
            
            return f"azure/{model_id}", config
        else:
            # Direct OpenAI - use per-request config
            if secrets.get("openai_key"):
                config = {"api_key": secrets["openai_key"]}
            return model_id, config
            
    elif is_bedrock_model(model_id):
        # Bedrock configuration - use environment variables for auth (not deprecated client param)
        region = os.environ.get("AWS_REGION", "us-east-1")
        config = {"aws_region_name": region}
        
        # Avoid double prefixing if model_id already has bedrock prefix
        if model_id.startswith("bedrock/"):
            return model_id, config
            
        # Use converse route for newer Claude models that require it
        if model_id.startswith("us.anthropic.claude"):
            return f"bedrock/converse/{model_id}", config
        else:
            return f"bedrock/{model_id}", config
        
    elif is_gemini_model(model_id):
        # Gemini configuration - use per-request config
        if secrets.get("gemini_key"):
            config = {"api_key": secrets["gemini_key"]}
        return f"gemini/{model_id}", config
        
    else:
        raise ValueError(f"Unsupported model: {model_id}")


def get_thinking_messages() -> list:
    """Get random thinking messages for status updates"""
    messages = [
        "I'm working on this...",
        "Let me think about this...", 
        "Processing your request...",
        "Analyzing the information...",
        "Generating response...",
        "Almost there..."
    ]
    import random
    return random.choice(messages)


def handle_reasoning_tokens(chunk: Dict[str, Any], request_id: str = None) -> None:
    """Handle reasoning tokens from different providers"""
    # OpenAI reasoning tokens
    if hasattr(chunk, 'type') and chunk.type == "response.reasoning_summary_text.delta":
        output_message({
            "type": "status",
            "data": {
                "id": "reasoning",
                "summary": "Thinking Details:",
                "message": chunk.delta,
                "icon": "bolt",
                "inProgress": True,
                "animated": True
            }
        }, request_id)
    
    # Bedrock reasoning tokens
    elif hasattr(chunk, 'delta') and hasattr(chunk.delta, 'reasoningContent'):
        output_message({
            "type": "status", 
            "data": {
                "id": "reasoning",
                "summary": "Thinking Details:",
                "message": chunk.delta.reasoningContent.text,
                "icon": "bolt",
                "inProgress": True,
                "animated": True
            }
        }, request_id)


def setup_reasoning_config(model: Dict[str, Any], chat_request: Dict[str, Any], model_str: str) -> Dict[str, Any]:
    """Setup reasoning configuration based on model and provider"""
    if not model.get("supportsReasoning"):
        return {}
        
    # Get reasoning level from options
    reasoning_level = chat_request.get("options", {}).get("reasoningLevel", "low")
    max_tokens = chat_request.get("max_tokens", 1000)
    
    # Budget tokens calculation (from params.js pattern)
    budget_tokens = 1024
    if reasoning_level == "medium":
        budget_tokens = 2048
    elif reasoning_level == "high":
        budget_tokens = 4096
        
    if budget_tokens > max_tokens:
        budget_tokens = max(max_tokens // 2, 1024)
    
    if "azure/" in model_str or is_openai_model(model["id"]):
        # OpenAI reasoning format
        if "/completions" in model_str:
            return {"reasoning_effort": reasoning_level}
        else:
            return {"reasoning": {"effort": reasoning_level, "summary": "auto"}}
            
    elif "bedrock/" in model_str:
        # Bedrock reasoning - LiteLLM handles reasoning tokens automatically for supported models
        # No additional configuration needed, reasoning tokens are captured in usage
        return {}
        
    elif "gemini/" in model_str:
        # Gemini reasoning format
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


def process_streaming_response(response: Iterator, model: Dict[str, Any], request_id: str = None) -> None:
    """Process streaming response from LiteLLM"""
    try:
        for chunk in response:
            # Handle different chunk types
            if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                choice = chunk.choices[0]
                
                # Handle content delta
                if hasattr(choice, 'delta') and hasattr(choice.delta, 'content') and choice.delta.content:
                    output_message({
                        "type": "content",
                        "data": choice.delta.content
                    }, request_id)
                
                # Handle complete message (for o1 models)
                elif hasattr(choice, 'message') and hasattr(choice.message, 'content') and choice.message.content:
                    output_message({
                        "type": "content", 
                        "data": choice.message.content
                    }, request_id)
                    
            # Handle usage information
            if hasattr(chunk, 'usage') and chunk.usage:
                usage = chunk.usage
                completion_details = getattr(usage, 'completion_tokens_details', None)
                
                # If reasoning tokens are present, show thinking status
                if completion_details and hasattr(completion_details, 'reasoning_tokens') and completion_details.reasoning_tokens > 0:
                    output_message({
                        "type": "status",
                        "data": {
                            "id": "reasoning",
                            "summary": f"Reasoning tokens: {completion_details.reasoning_tokens}",
                            "message": "Model used advanced reasoning",
                            "icon": "bolt", 
                            "inProgress": False,
                            "animated": False
                        }
                    }, request_id)
                
                # Send usage data for billing/accounting
                usage_data = {
                    "prompt_tokens": getattr(usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(usage, 'completion_tokens', 0),
                    "total_tokens": getattr(usage, 'total_tokens', 0)
                }
                
                # Add cached tokens if available
                prompt_details = getattr(usage, 'prompt_tokens_details', None)
                if prompt_details and hasattr(prompt_details, 'cached_tokens'):
                    usage_data["cached_tokens"] = prompt_details.cached_tokens
                else:
                    usage_data["cached_tokens"] = 0
                
                # Add reasoning tokens if available
                if completion_details and hasattr(completion_details, 'reasoning_tokens'):
                    usage_data["reasoning_tokens"] = completion_details.reasoning_tokens
                
                # Add detailed token breakdowns
                if prompt_details:
                    usage_data["prompt_tokens_details"] = {
                        "cached_tokens": getattr(prompt_details, 'cached_tokens', 0)
                    }
                    
                if completion_details:
                    usage_data["completion_tokens_details"] = {
                        "reasoning_tokens": getattr(completion_details, 'reasoning_tokens', 0)
                    }
                
                output_message({
                    "type": "usage",
                    "data": usage_data
                }, request_id)
            
            # Handle reasoning tokens from different providers
            handle_reasoning_tokens(chunk, request_id)
            
    except Exception as e:
        output_message({
            "type": "error",
            "data": {
                "statusCode": 500,
                "message": f"Streaming error: {str(e)}"
            }
        }, request_id)


def send_periodic_status(message: str = None, request_id: str = None) -> None:
    """Send periodic status updates during long operations"""
    if not message:
        message = get_thinking_messages()
        
    output_message({
        "type": "status",
        "data": {
            "id": "processing",
            "animated": True,
            "inProgress": True,
            "sticky": True,
            "summary": message,
            "icon": "info"
        }
    }, request_id)


def fetch_s3_content(s3_url):
    """Fetch content from S3 URL"""
    try:
        # Extract key from s3://bucket/key format
        if not s3_url.startswith("s3://"):
            return None
            
        # Get bucket name from environment
        bucket_name = os.environ.get("S3_FILE_TEXT_BUCKET_NAME")
        if not bucket_name:
            logger.error("S3_FILE_TEXT_BUCKET_NAME not set")
            return None
            
        # Extract key (remove s3:// prefix)
        key = s3_url[5:]  # Remove "s3://"
        
        # Create S3 client
        s3_client = boto3.client('s3')
        
        # Fetch object
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        content = response['Body'].read().decode('utf-8')
        
        logger.debug(f"Successfully fetched S3 content, length: {len(content)}")
        return content
        
    except Exception as e:
        logger.error(f"Failed to fetch S3 content from {s3_url}: {str(e)}")
        return None


def process_datasources(messages, data_sources):
    """Process dataSources and append their content to messages"""
    if not data_sources or len(data_sources) == 0:
        return messages
        
    # Build context from dataSources
    context_parts = []
    
    for ds in data_sources:
        if hasattr(ds, 'dict') and callable(ds.dict):
            ds = ds.dict()
        elif not isinstance(ds, dict):
            continue
            
        content = None
        
        # Try to get existing content
        if 'content' in ds and ds['content']:
            content = ds['content']
        elif 'text' in ds and ds['text']:
            content = ds['text']
        # Try to fetch from S3 if id looks like S3 URL
        elif 'id' in ds and ds['id'] and ds['id'].startswith('s3://'):
            logger.debug(f"Fetching S3 content for {ds['id']}")
            content = fetch_s3_content(ds['id'])
        
        if content:
            name = ds.get('name', ds.get('id', 'Document'))
            context_parts.append(f"--- {name} ---\n{content}")
            logger.debug(f"Added content for {name}, length: {len(content)}")
    
    if context_parts:
        context_message = {
            "role": "system", 
            "content": "Here are the attached documents to reference:\n\n" + "\n\n".join(context_parts)
        }
        # Insert context before the last user message
        messages = messages.copy()
        messages.insert(-1, context_message)
        logger.debug(f"Added system message with {len(context_parts)} documents")
    
    return messages


def get_memory_usage():
    """Get current memory usage in MB using built-in resource module"""
    try:
        # Get memory usage in KB and convert to MB
        memory_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On macOS, ru_maxrss is in bytes, on Linux it's in KB
        if sys.platform == 'darwin':  # macOS
            memory_mb = round(memory_kb / 1024 / 1024, 1)
        else:  # Linux
            memory_mb = round(memory_kb / 1024, 1)
        
        return {
            'rss': memory_mb,
            'vms': memory_mb,  # Simplified - using same value
            'percent': 0  # Not available with resource module
        }
    except Exception as e:
        logger.error(f"Memory monitoring error: {e}")
        return {'rss': 0, 'vms': 0, 'percent': 0}


def process_single_request(input_data: Dict[str, Any]) -> None:
    """Process a single LiteLLM request with dynamic model switching"""
    request_id = input_data.get("requestId", "unknown")
    
    # Log concurrent model usage for debugging
    logger.info(f"[CONCURRENT] Processing request {request_id} with model: {input_data.get('model', {}).get('id', 'unknown')}")
    
    try:
        chat_request = input_data["chatRequest"]
        model = input_data["model"]
        account = input_data["account"]
        secrets = input_data["secrets"]
        data_sources = input_data.get("dataSources", [])
        
        # Send initial status  
        # send_periodic_status("Initializing LiteLLM...", request_id)
        
        # Configure LiteLLM for the provider (now returns tuple with per-request config)
        model_str, provider_config = configure_litellm(model, secrets)
        
        config_memory = get_memory_usage()
        logger.debug(f"[MEMORY] After LiteLLM config - RSS: {config_memory['rss']}MB (+{config_memory['rss'] - initial_memory['rss']}MB)")
        logger.debug(f"Using model: {model_str} with per-request config")
        
        # Setup reasoning configuration
        reasoning_config = setup_reasoning_config(model, chat_request, model_str)
        
        # Debug: Check what dataSources we received
        logger.debug(f"Received {len(data_sources)} dataSources")
        for i, ds in enumerate(data_sources):
            logger.debug(f"DataSource {i}: {type(ds)} - Keys: {list(ds.keys()) if isinstance(ds, dict) else 'Not a dict'}")
            if isinstance(ds, dict):
                logger.debug(f"DataSource {i} content length: {len(str(ds.get('content', ''))) if ds.get('content') else 0}")
                logger.debug(f"DataSource {i} text length: {len(str(ds.get('text', ''))) if ds.get('text') else 0}")
        
        # Process dataSources and add their content to messages
        if data_sources:
            logger.debug(f"Processing {len(data_sources)} dataSources")
            output_message({
                "type": "status",
                "data": {
                    "id": "datasources",
                    "summary": f"Processing {len(data_sources)} attached documents",
                    "message": "Adding document content to conversation",
                    "icon": "document",
                    "inProgress": True,
                    "animated": True
                }
            }, request_id)
        
        messages = process_datasources(chat_request["messages"], data_sources)
        
        # Debug: Check if messages were modified
        original_count = len(chat_request["messages"])
        new_count = len(messages)
        logger.debug(f"Message count changed from {original_count} to {new_count}")
        if new_count > original_count:
            logger.debug(f"Added message content preview: {messages[-2]['content'][:200]}...")
        
        # Filter out any messages with empty content to prevent Bedrock errors
        filtered_messages = []
        for msg in messages:
            if isinstance(msg.get("content"), str) and msg["content"].strip():
                filtered_messages.append(msg)
            elif isinstance(msg.get("content"), list) and msg["content"]:
                # Handle structured content (images, etc.)
                filtered_messages.append(msg)
            else:
                logger.debug(f"Skipping message with empty content: role={msg.get('role')}")
        
        # Prepare completion parameters
        completion_params = {
            "model": model_str,
            "messages": filtered_messages,
            "stream": True,
            "max_tokens": chat_request.get("max_tokens", 1000),
            "temperature": chat_request.get("temperature", 1.0)
        }
        
        # Add provider-specific configuration (per-request to support concurrent models)
        completion_params.update(provider_config)
        
        # Add reasoning configuration
        completion_params.update(reasoning_config)
        
        # Add tools if present
        if "tools" in chat_request.get("options", {}):
            completion_params["tools"] = chat_request["options"]["tools"]
            
        # Add function calling ONLY if functions are also present
        if "functions" in chat_request:
            completion_params["functions"] = chat_request["functions"]
            # Only add function_call if functions exist
            if "function_call" in chat_request.get("options", {}):
                completion_params["function_call"] = chat_request["options"]["function_call"]
            
        # Add tool choice if present (only with tools)
        if "tool_choice" in chat_request.get("options", {}) and "tools" in completion_params:
            completion_params["tool_choice"] = chat_request["options"]["tool_choice"]
        
        # Debug: Check for problematic parameters
        if "function_call" in completion_params and "functions" not in completion_params:
            logger.warning("Removing function_call without functions")
            del completion_params["function_call"]
        
        if "tool_choice" in completion_params and "tools" not in completion_params:
            logger.warning("Removing tool_choice without tools")
            del completion_params["tool_choice"]
        
        # Status removed - LLM call happens immediately
        
        # Make the completion call
        pre_completion_memory = get_memory_usage()
        logger.debug(f"[MEMORY] Before LiteLLM completion - RSS: {pre_completion_memory['rss']}MB")
        logger.debug(f"Completion params: model={model_str}, has_api_key={'api_key' in provider_config}, has_api_base={'api_base' in provider_config}")
        logger.debug(f"Function calling: has_functions={'functions' in completion_params}, has_function_call={'function_call' in completion_params}, has_tools={'tools' in completion_params}")
        
        response = completion(**completion_params)
        
        # Process streaming response
        process_streaming_response(response, model, request_id)
        
        # Send end signal
        output_message({"type": "end"}, request_id)
        
        final_memory = get_memory_usage()
        total_increase = final_memory['rss'] - initial_memory['rss']
        logger.debug(f"[MEMORY] Python processing complete - RSS: {final_memory['rss']}MB (+{total_increase}MB total)")
        
    except Exception as e:
        # Send error message
        output_message({
            "type": "error", 
            "data": {
                "statusCode": 400,
                "message": f"LiteLLM Error: {str(e)}"
            }
        })
        # Send end signal after error
        output_message({"type": "end"})
        traceback.print_exc(file=sys.stderr)
        # Exit with 0 since we handled the error gracefully
        sys.exit(0)


def main():
    """Main server loop for persistent LiteLLM process"""
    import time
    startup_start = time.time()
    
    logger.debug("[TIMING] Python server measuring startup time...")
    
    # Track initial memory usage
    memory_start = time.time()
    global initial_memory
    initial_memory = get_memory_usage()
    memory_time = time.time() - memory_start
    
    total_startup = time.time() - startup_start
    
    logger.info(f"[MEMORY] LiteLLM Python server started - RSS: {initial_memory['rss']}MB")
    logger.info(f"[TIMING] Python server startup breakdown: total={total_startup:.3f}s, memory={memory_time:.3f}s")
    
    # Signal that server is ready to receive requests
    output_message({"type": "ready", "data": {
        "serverStarted": True, 
        "memoryUsage": initial_memory,
        "startupTiming": {
            "totalStartup": total_startup,
            "memoryCheck": memory_time
        }
    }})
    sys.stdout.flush()
    
    # Main server loop
    while True:
        try:
            # Read JSON request from stdin
            line = sys.stdin.readline()
            if not line:
                # EOF reached, exit gracefully
                break
                
            line = line.strip()
            if not line:
                continue
                
            # Parse the JSON input
            input_data = json.loads(line)
            
            # Process the request
            process_single_request(input_data)
            
        except json.JSONDecodeError as e:
            output_message({
                "type": "error",
                "data": {
                    "statusCode": 400,
                    "message": f"Invalid JSON input: {str(e)}"
                }
            })
            output_message({"type": "end"})
            
        except KeyboardInterrupt:
            logger.info("LiteLLM server shutting down...")
            break
            
        except Exception as e:
            output_message({
                "type": "error",
                "data": {
                    "statusCode": 500,
                    "message": f"Server error: {str(e)}"
                }
            })
            output_message({"type": "end"})
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()