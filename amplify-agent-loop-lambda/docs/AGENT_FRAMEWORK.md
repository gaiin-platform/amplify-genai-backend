# Understanding the Agent Framework: An Implementation Deep Dive

## Part 1: The Agent Loop
At the heart of the framework is the agent loop in `agent/core.py`. This is where the GAME (Goal, Action, Memory, Environment) loop executes:

```python
def run(self, user_input: str, memory=None, action_context_props=None) -> Memory:
    memory = memory or Memory()
    action_context = ActionContext({
        'environment': self.environment,
        'action_registry': self.actions,
        'memory': memory,
        'llm': self.generate_response,
        **user_action_context_props
    })

    # Record initial task
    self.set_current_task(action_context, memory, user_input)

    # Initialize capabilities
    for capability in self.capabilities:
        capability.init(self, action_context)

    while True:
        if iterations > self.max_iterations:
            break

        # 1. Build prompt with current context
        prompt = self.construct_prompt(action_context, self.goals, memory)

        # 2. Get next action from LLM
        response = self.prompt_llm_for_action(action_context, prompt)

        # 3. Execute action and get result
        result = self.handle_agent_response(
            action_context=action_context,
            response=response
        )

        # 4. Update memory with result
        self.update_memory(action_context, memory, response, result)

        # 5. Check for termination
        if self.should_terminate(action_context, response):
            break

    return memory
```

## Part 2: Tools and Registration

Tools are registered using the `@register_tool` decorator:

```python
@register_tool(
    tool_name="example_tool",
    description="Example tool description",
    parameters_override={
        "type": "object",
        "properties": {
            "param1": {"type": "string"}
        }
    },
    tags=["example"],
    status="Running {param1}...",
    resultStatus="Completed {param1}"
)
def example_tool(param1: str, action_context=None) -> str:
    return f"Processed {param1}"
```

The decorator implementation:

```python
def register_tool(tool_name=None, description=None, parameters_override=None, 
                 terminal=False, tags=None, status=None, resultStatus=None):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            action_context = kwargs.get('action_context')
            send_event = action_context.incremental_event()
            
            # Pre-execution status
            if status:
                send_event("agent/status", 
                          {"status": status.format(**kwargs)})
            
            # Execute tool
            result = func(*args, **kwargs)
            
            # Post-execution status
            if resultStatus:
                send_event("agent/status", 
                          {"status": resultStatus.format(**kwargs)})
            
            return result

        # Register tool metadata
        metadata = get_tool_metadata(
            func=wrapper,
            tool_name=tool_name,
            description=description,
            parameters_override=parameters_override,
            tags=tags
        )
        
        tools[metadata["tool_name"]] = metadata
        return wrapper
    return decorator
```

## Part 3: Action Registry and Environment

The `PythonActionRegistry` manages available tools:

```python
class PythonActionRegistry(ActionRegistry):
    def __init__(self, tags=None, tool_names=None):
        super().__init__()
        self.load_tools(tags, tool_names)

    def load_tools(self, tags=None, tool_names=None):
        for name, tool in tools.items():
            if self._should_load_tool(tool, tags, tool_names):
                self.register(Action(
                    name=tool["tool_name"],
                    function=tool["function"],
                    description=tool["description"],
                    parameters=tool["parameters"]
                ))

    def _should_load_tool(self, tool, tags, tool_names):
        if not tags and not tool_names:
            return True
        if tool_names and tool["tool_name"] in tool_names:
            return True
        if tags:
            return any(tag in tool["tags"] for tag in tags)
        return False
```

The `PythonEnvironment` executes actions:

```python
class PythonEnvironment(Environment):
    def execute_action(self, agent, action_context: ActionContext, 
                      action: Action, args: dict) -> dict:
        try:
            # Add context to args if needed
            args_copy = args.copy()
            if "action_context" in inspect.signature(action.function).parameters:
                args_copy["action_context"] = action_context

            # Execute and format result
            result = action.execute(**args_copy)
            return self.format_result(action, result)

        except Exception as e:
            return {
                "tool": action.name,
                "tool_executed": False,
                "error": str(e)
            }
```

## Part 4: Memory Management

The `Memory` class tracks conversation history:

```python
class Memory:
    def __init__(self):
        self.items = []

    def add_memory(self, memory: dict):
        self.items.append(memory)

    def get_memories(self, limit: int = None) -> List[Dict]:
        return self.items[:limit]

    def copy_without_system_memories(self):
        filtered_items = [m for m in self.items if m["type"] != "system"]
        memory = Memory()
        memory.items = filtered_items
        return memory
```

## Part 5: Capabilities

Capabilities extend agent functionality:

```python
class PlanFirstCapability(Capability):
    def process_prompt(self, agent, action_context: ActionContext, 
                      prompt: Prompt) -> Prompt:
        prompt.add_system_message(
            "First, create a plan. Then execute it step by step."
        )
        return prompt

class TimeAwareCapability(Capability):
    def init(self, agent, action_context: ActionContext):
        action_context.set("start_time", time.time())

    def should_terminate(self, agent, action_context: ActionContext, 
                        response: str) -> bool:
        elapsed = time.time() - action_context.get("start_time")
        return elapsed > agent.max_duration_seconds
```

Usage in agent construction:

```python
agent = Agent(
    goals=[Goal(name="Task", description="Complete the task")],
    agent_language=AgentFunctionCallingActionLanguage(),
    action_registry=action_registry,
    generate_response=llm,
    environment=PythonEnvironment(),
    capabilities=[
        TimeAwareCapability(),
        PlanFirstCapability()
    ]
)
```

This framework provides a flexible, extensible system for building agents that can:
- Use registered tools
- Maintain conversation state
- Execute in controlled environments
- Track and manage resources
- Handle complex, multi-step tasks

The modular design allows for easy addition of new tools, capabilities, and environment modifications while maintaining a consistent execution pattern.


# Essential Tools: Common Tools Package

## Overview
The `agent.common_tools` package provides essential tools that every agent needs, particularly the `terminate` tool that properly ends agent execution.

## Importing Common Tools
Always include this import in your agent setup:

```python
import agent.common_tools  # This registers the terminate tool
```

## The Terminate Tool
The `terminate` tool is crucial for proper agent execution:

```python
@register_tool(terminal=True)
def terminate(message: str, result_references: List = None):
    """
    Terminate the conversation and return final results.
    """
    return {
        "message": message,
        "results": result_references
    }
```

### Usage by Agent
```python
# Agent using terminate
response = {
    "tool": "terminate",
    "args": {
        "message": "Task completed! Here are the results.",
        "result_references": ["$#0", "$#1"]  # References to previous tool results
    }
}
```

### Why It's Important
1. **Clean Termination**: Signals task completion
2. **Result Collection**: Gathers important outputs
3. **Reference System**: Can include previous tool results
4. **Clear Communication**: Provides final message to user

## Best Practices
- Always import `agent.common_tools`
- Use `terminate` to end agent execution
- Include relevant result references
- Provide clear completion messages

The `terminate` tool ensures proper task completion and result reporting, making it an essential part of any agent implementation.

# Understanding Agent Memory and Output

## Basic Usage
When you run an agent, it maintains a conversation history in its memory:

```python
# Create a session
memory = agent.run(
    user_input="Please greet John",
    action_context_props={
        "current_user": "user123",
        "session_id": "abc123",
        "access_token": "token123"
    }
)
```

## Memory Structure
The memory contains a list of interactions in chronological order:

```python
memory.items = [
    # User's initial request
    {
        "role": "user",
        "content": "Get a list of files in the current directory..."
    },
    
    # System's planning message
    {
        "role": "system",
        "content": "You must follow these instructions carefully...\n1. List files..."
    },
    
    # Assistant's tool selection
    {
        "role": "assistant",
        "content": {
            "tool": "exec_code",
            "args": {
                "code": "import os\ntry:\n    files = os.listdir('.')..."
            }
        }
    },
    
    # Environment's response (tool execution result)
    {
        "role": "environment",
        "content": {
            "tool_executed": true,
            "result": ["file1.txt", "file2.txt"],
            "id": "$#0",
            "timestamp": "2025-02-12T14:50:49+0000"
        }
    }
]
```

## Memory Roles

1. **user**: Original requests
   ```python
   {"role": "user", "content": "Please help me..."}
   ```

2. **system**: Planning and instruction messages
   ```python
   {"role": "system", "content": "Step 1: First, we will..."}
   ```

3. **assistant**: Tool selections and actions
   ```python
   {
       "role": "assistant",
       "content": {
           "tool": "greet_user",
           "args": {"name": "John"}
       }
   }
   ```

4. **environment**: Tool execution results
   ```python
   {
       "role": "environment",
       "content": {
           "tool_executed": true,
           "result": "Hello, John!",
           "id": "$#0",
           "timestamp": "2024-01-20T10:00:00Z"
       }
   }
   ```

## Key Features

1. **Conversation Tracking**
   - Complete history of interactions
   - Tool executions and results
   - System planning messages
   - Timestamps on actions

2. **Tool Execution Records**
   - Tool name and arguments
   - Execution success/failure
   - Result data
   - Unique execution IDs

3. **Error Handling**
   ```python
   {
       "role": "environment",
       "content": {
           "tool_executed": false,
           "error": "Failed to execute tool",
           "id": "$#1"
       }
   }
   ```

4. **Reference System**
   - Each tool execution gets a unique ID (`$#0`, `$#1`, etc.)
   - Results can be referenced in later tool calls
   - Timestamps for execution tracking

This memory system provides:
- Complete audit trail
- Tool execution history
- Error tracking
- Result references
- Conversation context

The memory can be saved, restored, and used to resume sessions or analyze agent behavior.

# Agent Languages: Communicating with LLMs

## Overview
Agent languages define how we structure prompts to LLMs and interpret their responses. The primary language is `AgentFunctionCallingActionLanguage`, which uses OpenAI's function calling format.

## Function Calling Language

```python
class AgentFunctionCallingActionLanguage(AgentLanguage):
    def format_actions(self, actions: List[Action]) -> [List,List]:
        # Convert actions to OpenAI function definitions
        tools = [
            {
                "type": "function",
                "function": {
                    "name": action.name,
                    "description": action.description[:1024],
                    "parameters": action.parameters,
                },
            } for action in actions
        ]
        return tools
```

The language expects responses in JSON format:
```json
{
    "tool": "tool_name",
    "args": {
        "param1": "value1",
        "param2": "value2"
    }
}
```

## Error Handling and Retries

When the agent provides an invalid response, the system retries with feedback:

```python
def prompt_llm_for_action(self, action_context: ActionContext, prompt: Prompt) -> str:
    max_tries = 3
    for i in range(max_tries):
        try:
            response = self.generate_response(prompt)
            # Parse into action
            action_def, action = self.get_action(response)
            return response

        except Exception as e:
            traceback_str = traceback.format_exc()
            
            # Adapt prompt with error feedback
            prompt = self.agent_language.adapt_prompt_after_parsing_error(
                prompt,
                response,
                traceback_str,
                e,
                (max_tries - i - 1)
            )
```

The function calling language handles errors by reminding the agent to use tools:

```python
def adapt_prompt_after_parsing_error(self, prompt: Prompt, 
                                   response: str, 
                                   traceback: str,
                                   error: Any, 
                                   retries_left: int) -> Prompt:
    new_messages = prompt.messages + [
        {"role": "assistant", "content": f"{response}"},
        {"role": "system", "content": "CRITICAL!!! You must ALWAYS choose a tool to use."},
        {"role": "user", "content": "You did not call a valid tool. "
                                   "Please choose an available tool and output a tool call."}
    ]
    return Prompt(messages=new_messages, tools=prompt.tools)
```

## Alternative Languages

### JSON Action Language
Uses explicit JSON blocks:
```python
class AgentJsonActionLanguage(AgentLanguage):
    action_format = """
<Think step by step>

```action
{
    "tool": "tool_name",
    "args": {...}
}
```"""
```

### Natural Language
Simplest format, converts all responses to termination:
```python
class AgentNaturalLanguage(AgentLanguage):
    def parse_response(self, response: str) -> dict:
        return {
            "tool": "terminate",
            "args": {
                "message": response
            }
        }
```

## Key Features

1. **Response Parsing**
   ```python
   def parse_response(self, response: str) -> dict:
       try:
           return json.loads(response)
       except Exception as e:
           if self.allow_non_tool_output:
               return {
                   "tool": "terminate",
                   "args": {"message": response}
               }
           raise ValueError(f"Invalid tool invocation: {str(e)}")
   ```

2. **Prompt Construction**
   ```python
   def construct_prompt(self, actions, environment, goals, memory) -> Prompt:
       prompt = []
       prompt += self.format_goals(goals)
       prompt += self.format_memory(memory)
       tools = self.format_actions(actions)
       return Prompt(messages=prompt, tools=tools)
   ```

3. **Memory Formatting**
   ```python
   def format_memory(self, memory: Memory) -> List:
       items = memory.get_memories()
       return to_json_memory_messages_format(items)
   ```

The function calling language is preferred because:
1. Native support in modern LLMs
2. Structured parameter validation
3. Clear action boundaries

# Writing Tools for Agents: A Guide

## Basic Tool Structure

The simplest tool uses the `@register_tool` decorator:

```python
from agent.components.tool import register_tool

@register_tool(
    tool_name="calculate_average",
    description="Calculate the average of a list of numbers",
    tags=["math"]
)
def calculate_average(numbers: list[float]) -> float:
    """Calculate the average of the provided numbers."""
    return sum(numbers) / len(numbers)
```

## Adding Status Updates

Tools can provide progress updates:

```python
@register_tool(
    tool_name="process_data",
    description="Process a large dataset",
    status="Processing {filename}...",
    resultStatus="Completed processing {filename}",
    errorStatus="Failed to process {filename}: {exception}"
)
def process_data(filename: str, action_context=None) -> dict:
    send_event = action_context.incremental_event()
    
    try:
        send_event("process/start", {"file": filename})
        # ... processing ...
        send_event("process/progress", {"complete": "50%"})
        # ... more processing ...
        return {"processed": True}
    except Exception as e:
        send_event("process/error", {"error": str(e)})
        raise
```

## Working with Files

Tools that handle files should use the work directory:

```python
@register_tool(
    tool_name="save_chart",
    description="Create and save a chart from data"
)
def save_chart(data: list, filename: str, action_context=None) -> dict:
    import matplotlib.pyplot as plt
    
    # Get the work directory from context
    work_dir = action_context.get("work_directory")
    file_path = os.path.join(work_dir, filename)
    
    # Create chart
    plt.figure()
    plt.plot(data)
    plt.savefig(file_path)
    plt.close()
    
    return {
        "file_saved": filename,
        "path": file_path
    }
```

## Using Other Tools

Tools can access other tools through the action registry:

```python
@register_tool(
    tool_name="analyze_and_chart",
    description="Analyze data and create a chart"
)
def analyze_and_chart(data: list, action_context=None) -> dict:
    # Get registry from context
    registry = action_context.get_action_registry()
    
    # Use other tools
    stats_tool = registry.get_action("calculate_stats")
    chart_tool = registry.get_action("save_chart")
    
    # Execute tools
    stats = stats_tool.execute(data=data)
    chart = chart_tool.execute(
        data=data,
        filename="analysis.png",
        action_context=action_context
    )
    
    return {
        "statistics": stats,
        "chart": chart["file_saved"]
    }
```

## Parameter Validation

Define clear parameter schemas:

```python
@register_tool(
    tool_name="format_text",
    description="Format text with specified options",
    parameters_override={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to format"
            },
            "style": {
                "type": "string",
                "enum": ["UPPER", "lower", "Title"],
                "description": "Formatting style to apply"
            }
        },
        "required": ["text", "style"]
    }
)
def format_text(text: str, style: str) -> str:
    if style == "UPPER":
        return text.upper()
    elif style == "lower":
        return text.lower()
    return text.title()
```

## Context-Aware Tools

Access user and session information:

```python
@register_tool(
    tool_name="get_user_preferences",
    description="Get user preferences"
)
def get_user_preferences(action_context=None, _current_user=None) -> dict:
    # _current_user is automatically injected from context
    return {
        "user": _current_user,
        "session": action_context.get("session_id"),
        "preferences": load_user_prefs(_current_user)
    }
```

## LLM Integration

Tools can use the LLM:

```python
@register_tool(
    tool_name="summarize_text",
    description="Create a summary of the provided text"
)
def summarize_text(text: str, action_context=None) -> str:
    # Get LLM function from context
    llm = action_context.get("llm")
    
    response = llm([{
        "role": "user",
        "content": f"Summarize this text: {text}"
    }])
    
    return {"summary": response}
```

## Terminal Tools

Tools that should end the agent's execution:

```python
@register_tool(
    tool_name="complete_task",
    description="Complete the current task",
    terminal=True  # This ends agent execution
)
def complete_task(message: str) -> dict:
    return {
        "status": "complete",
        "message": message
    }
```

## Best Practices

1. **Clear Descriptions**
   ```python
   @register_tool(
       tool_name="process_data",
       description="""
       Process data with the following steps:
       1. Validate input
       2. Clean data
       3. Calculate statistics
       Accepts: CSV or JSON data
       Returns: Analysis results
       """
   )
   ```

2. **Error Handling**
   ```python
   @register_tool(tool_name="safe_operation")
   def safe_operation(data: dict, action_context=None) -> dict:
       try:
           result = process(data)
           return {"success": True, "result": result}
       except ValueError as e:
           return {"success": False, "error": str(e)}
       except Exception as e:
           action_context.send_event("error", {"error": str(e)})
           raise
   ```

3. **Progress Updates**
   ```python
   @register_tool(tool_name="long_process")
   def long_process(action_context=None) -> dict:
       send_event = action_context.incremental_event()
       steps = ["start", "process", "finish"]
       
       for step in steps:
           send_event("status", {"step": step})
           # ... processing ...
   ```

These practices create tools that are:
- Easy for agents to use
- Provide clear feedback
- Handle errors gracefully
- Maintain state properly
- Integrate well with other tools

# Creating Tools: The Preferred Approach

## Simple Tool Registration

The preferred way to create tools is to let the system automatically extract metadata from the function's type hints and docstring:

```python
@register_tool()
def calculate_statistics(data: list[float]) -> dict:
    """Calculate basic statistical measures for a dataset.
    
    The function computes mean, median, and standard deviation.
    
    Args:
        data: List of numerical values to analyze
        
    Returns:
        dict: Statistics including:
            - mean: average value
            - median: middle value
            - std: standard deviation
    """
    import numpy as np
    return {
        "mean": np.mean(data),
        "median": np.median(data),
        "std": np.std(data)
    }
```

The decorator automatically:
1. Uses function name as tool_name
2. Extracts description from docstring
3. Builds parameter schema from type hints
4. Sets up event tracking

## Automatic Event Generation

Every tool automatically emits these events:

```python
# When tool starts
tools/{tool_name}/start
{
    "args": {arg1: value1, arg2: value2},
    "context_id": "uuid",
    "timestamp": "2024-01-20T10:00:00Z"
}

# When tool completes successfully
tools/{tool_name}/end
{
    "args": {arg1: value1, arg2: value2},
    "result": tool_result,
    "context_id": "uuid",
    "timestamp": "2024-01-20T10:00:01Z"
}

# If tool errors
tools/{tool_name}/error
{
    "args": {arg1: value1, arg2: value2},
    "exception": "error message",
    "traceback": "stack trace",
    "context_id": "uuid",
    "timestamp": "2024-01-20T10:00:01Z"
}
```

## Adding Status Messages (Optional)

If you want additional status updates:

```python
@register_tool(
    status="Processing {filename}...",
    resultStatus="Processed {filename}",
    errorStatus="Failed to process {filename}: {exception}"
)
def process_file(filename: str, action_context=None) -> dict:
    """Process a file and return analysis results.
    
    Args:
        filename: Name of file to process
        action_context: Automatically injected context
    
    Returns:
        dict: Processing results
    """
    # These status messages will be sent as agent/status events
    return {"processed": True}
```

## Custom Events

Need more detailed progress? Use `incremental_event`:

```python
@register_tool()
def complex_analysis(data: list, action_context=None) -> dict:
    """Perform complex analysis with multiple steps.
    
    Args:
        data: Data to analyze
        action_context: Automatically injected context
    """
    send_event = action_context.incremental_event({
        "analysis_id": str(uuid.uuid4())
    })
    
    # All these events will include analysis_id
    send_event("analysis/cleanup", {"step": "removing nulls"})
    send_event("analysis/process", {"progress": 50})
    send_event("analysis/complete", {"result": "success"})
    
    return {"completed": True}
```

## Best Practices

1. **Let Documentation Drive**
   ```python
   @register_tool()
   def analyze_text(text: str) -> dict:
       """Analyze text sentiment and key phrases.
       
       Performs sentiment analysis and extracts important phrases
       from the provided text.
       
       Args:
           text: The text to analyze
           
       Returns:
           dict: Analysis results containing:
               - sentiment: positive/negative score
               - key_phrases: list of important phrases
       """
       # Implementation...
   ```

2. **Use Type Hints**
   ```python
   from typing import List, Dict, Optional

   @register_tool()
   def filter_data(
       items: List[Dict],
       min_value: Optional[float] = None
   ) -> List[Dict]:
       """Filter a list of items by minimum value."""
       if min_value is None:
           return items
       return [i for i in items if i.get('value', 0) >= min_value]
   ```

3. **Accept Context When Needed**
   ```python
   @register_tool()
   def save_result(
       data: dict,
       filename: str,
       action_context=None
   ) -> dict:
       """Save analysis results to a file."""
       work_dir = action_context.get("work_directory")
       path = os.path.join(work_dir, filename)
       # ... save data ...
   ```

This approach:
- Keeps documentation and code in sync
- Provides automatic event tracking
- Reduces boilerplate
- Makes tools self-documenting
- Maintains clean parameter validation

The registration system handles all the complexity while you focus on writing clear, well-documented functions.


# Understanding Ops Integration with Agents

## Overview
Ops (operations) are HTTP endpoints that get converted into tools that agents can use. The system automatically handles authentication and user context.

## Registration Process

```python
def register_op_actions(action_registry: ActionRegistry, 
                       access_token: str, 
                       current_user: str):
    # 1. Get available APIs
    apis = get_all_apis(action_context=ActionContext({
        "access_token": access_token,
        "current_user": current_user,
    }))
    
    # 2. Convert APIs to actions
    api_tools = ops_to_actions(apis)
    
    # 3. Register each action
    for action in api_tools:
        action_registry.register(action)
```

## Converting Ops to Tools

```python
def op_to_tool(api):
    name = api['name']
    id = api['id']
    
    # Create function that will invoke the API
    def api_func_invoke(action_context: ActionContext, **kwargs):
        return call_api(
            action_context=action_context, 
            name=name, 
            payload=kwargs
        )
    
    # Create tool metadata
    tool_metadata = get_tool_metadata(
        func=api_func,
        tool_name=id,
        description=api.get('description', ""),
        parameters_override=api.get('parameters', {}),
        tags=api.get('tags', [])
    )
    
    return tool_metadata
```

## API Call Handling

The core API caller injects authentication and context:

```python
@register_tool(tags=['ops'])
def call_api(action_context: ActionContext, name: str, payload: dict):
    # Extract authentication and context
    params = {
        'access_token': action_context.get('access_token'),
        'current_user': action_context.get('current_user'),
        'conversation_id': action_context.get('session_id'),
        'assistant_id': action_context.get('agent_id'),
        'message_id': action_context.get('message_id')
    }
    
    return execute_api_call(
        name=name,
        payload=payload,
        **params
    )
```

## Lambda Execution

The system executes ops through a Lambda function:

```python
def execute_api_call(name: str, payload: dict, 
                    access_token: str, current_user: str,
                    conversation_id: str, assistant_id: str,
                    message_id: str) -> dict:
    client = boto3.client('lambda')
    
    # Prepare event with authentication
    event = {
        'name': name,
        'payload': payload,
        'conversation': conversation_id,
        'message': message_id,
        'assistant': assistant_id,
        'token': access_token,
        'current_user': current_user
    }
    
    # Invoke Lambda
    response = client.invoke(
        FunctionName=os.environ['OPS_LAMBDA_NAME'],
        Payload=json.dumps(event)
    )
```

## Usage Example

When an agent uses an op-based tool:

```python
# 1. Op is registered
api = {
    'name': 'getDocuments',
    'id': 'getDocuments',
    'description': 'Get user documents',
    'parameters': {
        'type': 'object',
        'properties': {
            'folder': {'type': 'string'}
        }
    }
}

# 2. Becomes available as a tool
@register_tool(tool_name='getDocuments')
def get_documents(action_context, folder: str):
    return call_api(
        action_context=action_context,
        name='getDocuments',
        payload={'folder': folder}
    )

# 3. Agent uses the tool
agent_response = {
    "tool": "getDocuments",
    "args": {"folder": "reports"}
}

# 4. Tool executes with authentication
# - access_token from action_context
# - current_user from action_context
# - Automatically includes session/message IDs
```

Key points about the system:
1. Authentication is automatic
2. User context is maintained
3. Ops are discovered dynamically
4. Parameters are validated
5. Error handling is consistent
6. Execution is tracked

This allows agents to seamlessly use HTTP endpoints while maintaining proper authentication and context throughout the execution chain.

# Registering Operations as Agent Tools

## Overview
The `register_op_actions` function fetches available API operations and converts them into tools that agents can use. This is particularly useful for integrating HTTP endpoints or Lambda functions as agent tools.

## Basic Usage

```python
def setup_agent():
    # Create registry
    action_registry = PythonActionRegistry()
    
    # Register all available ops
    register_op_actions(
        action_registry=action_registry,
        access_token="user-token",
        current_user="user123"
    )
    
    # Registry now contains all available ops as tools
    return action_registry
```

## How It Works

```python
def register_op_actions(
    action_registry: ActionRegistry,
    access_token: str,
    current_user: str
):
    # 1. Fetch available APIs
    apis = get_all_apis(ActionContext({
        "access_token": access_token,
        "current_user": current_user,
    }))
    
    # 2. Convert to agent actions
    api_tools = ops_to_actions(apis)
    
    # 3. Register each action
    for action in api_tools:
        action_registry.register(action)
```

## Example API Operation

An operation might look like:
```json
{
    "name": "queryDatabase",
    "id": "queryDatabase",
    "description": "Query the database with SQL",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL query to execute"
            }
        },
        "required": ["query"]
    },
    "tags": ["database", "query"]
}
```

This becomes:
```python
# Automatically registered tool
@register_tool(
    tool_name="queryDatabase",
    description="Query the database with SQL",
    tags=["database", "query"]
)
def query_database(query: str, action_context=None):
    return call_api(
        action_context=action_context,
        name="queryDatabase",
        payload={"query": query}
    )
```

## Using with Assistants

When setting up an assistant with specific operations:

```python
def create_assistant_agent(assistant_ops, access_token, current_user):
    registry = PythonActionRegistry()
    
    # Register only specific ops
    api_tools = ops_to_tools(assistant_ops)
    for tool in api_tools:
        registry.register(Action(
            name=tool['tool_name'],
            function=tool['function'],
            description=tool['description'],
            parameters=tool['parameters']
        ))
    
    return create_agent(registry)
```

## Common Operations

Examples of typical operations that get registered:

```python
# File operations
{
    "name": "listFiles",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"}
        }
    }
}

# Database operations
{
    "name": "executeQuery",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"}
        }
    }
}

# API integrations
{
    "name": "callExternalApi",
    "parameters": {
        "type": "object",
        "properties": {
            "endpoint": {"type": "string"},
            "method": {"type": "string"}
        }
    }
}
```

## Authentication Flow

The registration process maintains authentication:

```python
# 1. Initial registration
register_op_actions(registry, token, user)

# 2. When agent uses tool
agent.run("Query the database")

# 3. Tool execution
def execute_api_call(name, payload, access_token, current_user, **kwargs):
    return client.invoke(
        FunctionName=os.environ['OPS_LAMBDA_NAME'],
        Payload=json.dumps({
            'name': name,
            'payload': payload,
            'token': access_token,
            'current_user': current_user,
            **kwargs
        })
    )
```

This system allows:
- Dynamic tool registration
- Automatic authentication
- Operation discovery
- Parameter validation
- Consistent error handling

Making external operations seamlessly available to agents while maintaining security and proper context.