# Building Your First Agent: A Tutorial

## Overview
Let's create a simple agent that can perform tasks using tools. The core agent framework is built around several key concepts:

1. Agents - Execute actions through tools to accomplish goals
2. Tools - Functions that agents can call to accomplish tasks
3. Goals - Instructions and constraints for the agent
4. Environment - Manages tool execution and results
5. Action Registry - Keeps track of available tools

## Creating a Simple Tool

Let's start by creating a tool that our agent can use:

```python
from agent.components.tool import register_tool
import agent.common_tools  # CRITICAL: This registers the terminate tool!! Always register this!

@register_tool(tags=["greeting"])
def greet_user(name: str) -> str:
    """
    Provides a greeting to the user.
    """
    return f"Hello, {name}!"
```

This creates a tool that:
- Has a name "greet_user"
- Takes a string parameter "name"
- Is tagged for organization
- Returns a greeting

## Building the Agent

Now let's create an agent that can use our tool:

```python
from agent.core import Goal
from agent.components.python_environment import PythonEnvironment
from agent.components.python_action_registry import PythonActionRegistry
from agent.agents.actions_agent import build_clean
import agent.common_tools  # This registers the terminate tool

# Create our goals
goals = [
    Goal(
        name="Friendly Assistant",
        description="You are a friendly assistant who helps users."
    )
]

# Create the environment and registry
environment = PythonEnvironment()
action_registry = PythonActionRegistry()

# Build the agent
agent = build_clean(
    environment=environment,
    action_registry=action_registry,
    generate_response=create_llm("some access token", "gpt-4o-mini"),
    additional_goals=goals
)
```

## Running the Agent

With our agent built, we can give it a task:

```python
# Create a session
memory = agent.run(
    user_input="Please greet John",
    action_context_props={
        # Anything you want your tools to be able to 
        # access can be passed here
        "current_user": "user123",
        "session_id": "abc123",
        "access_token": ...
    }
)

# The agent will:
# 1. Parse the request
# 2. Identify the greet_user tool
# 3. Call it with name="John"
# 4. Return the result
```

# Understanding Agent Memory Output

When you run this code, the memory will contain a sequence of interactions:

```python
memory.items = [
    # 1. User's original request
    {
        "role": "user",
        "content": "Please greet John"
    },
    
    # 2. System's messages (if any capabilities add this)
    {
        "role": "system",
        "content": "I will use the greet_user tool to greet John..."
    },
    
    # 3. Assistant's tool selection
    {
        "role": "assistant",
        "content": {
            "tool": "greet_user",
            "args": {
                "name": "John"
            }
        }
    },
    
    # 4. Environment's response (tool execution result)
    {
        "role": "environment",
        "content": {
            "tool_executed": true,
            "result": "Hello, John!",
            "id": "$#0",  # Reference ID for this result
            "timestamp": "2024-01-20T10:00:00Z"
        }
    },
    
    # 5. Assistant's termination with result
    {
        "role": "assistant",
        "content": {
            "tool": "terminate",
            "args": {
                "message": "I've greeted John for you!",
                "result_references": ["$#0"]  # References the greeting result
            }
        }
    },
    
    # 6. Environment's final response
    {
        "role": "environment",
        "content": {
            "tool_executed": true,
            "result": {
                "message": "I've greeted John for you!",
                "results": ["Hello, John!"]  # The referenced result
            },
            "id": "$#1",
            "timestamp": "2024-01-20T10:00:01Z"
        }
    }
]
```

## Memory Components Explained

1. **User Input** (`role: "user"`): The original request
2. **System Planning** (`role: "system"`): Added by planning capabilities
3. **Tool Execution** (`role: "assistant"`): The agent's chosen action
4. **Tool Result** (`role: "environment"`): Result of the tool execution
5. **Termination** (`role: "assistant"`): Final terminate tool call
6. **Final Result** (`role: "environment"`): The complete response

Key features:
- Each tool result gets a unique ID (`$#0`, `$#1`)
- Timestamps track execution time
- `result_references` link to previous results
- Final termination includes relevant results

This structured memory provides a complete record of the interaction, from initial request to final response.

## Adding Tool Status Updates

We can make our tool more informative by adding status updates:

```python
@register_tool(
    tool_name="greet_user",
    description="Greet a user by name",
    tags=["greeting"],
    status="Greeting {name}...",
    resultStatus="Greeted {name}"
)
def greet_user(name: str, action_context=None) -> str:
    return f"Hello, {name}!"
```

Now when the tool runs, it will:
1. Send "Greeting John..." status
2. Execute the greeting
3. Send "Greeted John" status

## Error Handling

Let's add error handling to our tool:

```python
@register_tool(
    tool_name="greet_user",
    description="Greet a user by name",
    tags=["greeting"],
    status="Greeting {name}...",
    resultStatus="Greeted {name}",
    errorStatus="Failed to greet {name}: {exception}"
)
def greet_user(name: str, action_context=None) -> str:
    if not name:
        raise ValueError("Name cannot be empty")
    return f"Hello, {name}!"
```

## Understanding the Core Components

The agent system works through several key classes:

1. `Agent` - The main controller that:
    - Manages the conversation loop
    - Executes actions
    - Maintains memory
    - Tracks goals

2. `PythonEnvironment` - Handles tool execution:
    - Manages tool arguments
    - Tracks results
    - Handles errors

3. `ActionRegistry` - Manages available tools:
    - Stores tool definitions
    - Provides tool lookup
    - Validates tool calls

4. `Memory` - Keeps track of:
    - Conversation history
    - Tool results
    - System messages


# Using Action Context in Tools

## Overview
The `action_context` provides tools with access to session state, user information, and shared resources. It's automatically injected into tools that request it and provides a standardized way to access contextual information.

## Basic Action Context Usage

```python
@register_tool(
    tool_name="welcome_user",
    description="Welcome the current user"
)
def welcome_user(action_context=None) -> str:
    # Access the current user from context
    current_user = action_context.get("current_user")
    return f"Welcome back, {current_user}!"
```

## Available Context Properties

Common properties available in `action_context`:
```python
action_context.properties = {
    "current_user": "user identifier",
    "access_token": "authentication token",
    "session_id": "current session",
    "work_directory": "path for file operations",
    "agent_registry": "access to other agents",
    "llm": "language model function",
    "environment": "execution environment",
    "memory": "conversation memory"
}
```

## Accessing Custom Properties

You can also access properties with an underscore prefix:

```python
@register_tool(
    tool_name="process_data",
    description="Process data with credentials"
)
def process_data(data: str, _credentials=None) -> str:
    # The _credentials parameter will be automatically filled
    # from action_context.properties["credentials"]
    api_key = _credentials.get("api_key")
    return f"Processing {data} with {api_key}"
```

## Event Tracking

Tools can send events through the context:

```python
@register_tool(
    tool_name="long_process",
    description="Run a long process with progress updates"
)
def long_process(action_context=None) -> str:
    # Create an event sender for this operation
    send_event = action_context.incremental_event()
    
    # Send progress updates
    send_event("process/start", {"status": "initializing"})
    send_event("process/step", {"progress": "50%"})
    send_event("process/complete", {"status": "done"})
    
    return "Process complete"
```

## Accessing Environment and Memory

Tools can interact with the environment and memory:

```python
@register_tool(
    tool_name="summarize_conversation",
    description="Summarize the conversation history"
)
def summarize_conversation(action_context=None) -> str:
    # Get memory from context
    memory = action_context.get_memory()
    
    # Get environment from context
    env = action_context.get_environment()
    
    # Access conversation history
    messages = memory.get_memories()
    
    return f"Found {len(messages)} messages in conversation"
```

## Using the Agent Registry

Tools can invoke other agents:

```python
@register_tool(
    tool_name="delegate_task",
    description="Delegate a task to another agent"
)
def delegate_task(task: str, action_context=None) -> str:
    # Get agent registry
    registry = action_context.get_agent_registry()
    
    # Find and use another agent
    math_agent = registry.get_agent("math_expert")
    if math_agent:
        return math_agent.run(task, action_context.get_memory())
```

## Event Correlation

Track related events with correlation IDs:

```python
@register_tool(
    tool_name="multi_step_process",
    description="Run a multi-step process"
)
def multi_step_process(action_context=None) -> str:
    # Create correlated event sender
    send_event = action_context.incremental_event({
        "process_id": "123"
    })
    
    # All events will include the process_id
    send_event("step1", {"status": "complete"})
    send_event("step2", {"status": "complete"})
    
    return "Process finished"
```

This context system provides tools with rich access to the agent's environment while maintaining clean separation of concerns and proper dependency injection.

# HTTP Interaction with the Agent System

## Overview
The `handle_event` function serves as the primary entry point for agent interactions via HTTP. When you use the chat interface in Amplify, each message triggers a call to this endpoint, which processes your request and manages the agent's response.

## Basic HTTP Request
To invoke the agent directly, send a POST request to `/vu-agent/handle-event`:

```python
{
    "sessionId": "unique-session-id",
    "prompt": [
        {"role": "user", "content": "Your message here"}
    ],
    "metadata": {
        "model": "gpt-4o"
    }
}
```

## Assistant Integration
When you're using an Amplify assistant, the request includes additional metadata about the assistant's capabilities and instructions:

```python
{
    "sessionId": "08c86afe-dbac-401d-9486-040fcef9e69d",
    "prompt": [...],
    "metadata": {
        "model": "gpt-4o",
        "builtInOperations": ["tag:code_exec"],
        "assistant": {
            "instructions": "Act really happy...",
            "data": {
                "operations": [
                    {
                        "name": "getGoogleSheetsInfo",
                        "description": "Returns information about Google Sheets...",
                        "parameters": {...}
                    },
                    // Additional operations...
                ]
            }
        }
    }
}
```

## What Happens Inside handle_event

1. **Tool Configuration**
   ```python
   # Built-in tools are loaded based on tags
   built_in_operations = metadata.get('builtInOperations', [])
   
   # Assistant operations are converted to tools
   ops = assistant.get("data",{}).get("operations", [])
   op_tools = ops_to_tools(ops)
   ```

2. **Assistant Integration**
   ```python
   # Assistant instructions become goals
   if assistant['instructions']:
       additional_goals.append(Goal(
           name="Instructions:",
           description=assistant['instructions']
       ))
   
   # Assistant operations become available tools
   for op_tool in op_tools:
       action_registry.register(Action(
           name=op_tool['tool_name'],
           function=op_tool["function"],
           description=op_tool["description"],
           ...
       ))
   ```

3. **Conversation Management**
   ```python
   # The prompt is processed to maintain conversation context
   user_input = "\n".join([
       f"{entry['role']}: {entry['content']}" 
       for entry in prompt
   ])
   
   # The agent processes the input with all available tools
   result = agent.run(
       user_input=user_input,
       action_context_props=action_context_props
   )
   ```

## Example Usage
In your example request, you're:
1. Using the code execution tools (`tag:code_exec`)
2. Adding custom happiness instructions to the agent
3. Making Google Sheets operations available
4. Including LLM query capabilities

The agent will:
1. List directory contents using code execution tools
2. Calculate filename lengths
3. Respond with enthusiasm (per assistant instructions)
4. Have access to Google Sheets operations if needed

The response includes:
- Conversation history
- Any file changes
- Tool execution results
- State management information

This unified interface allows Amplify to seamlessly integrate different assistants while maintaining consistent interaction patterns and state management.

# Understanding the Agent's File State

## Overview
When an agent creates, modifies, or reads files during execution (e.g., through code execution or file operations), all changes are tracked and persisted across invocations using the session ID as the key identifier.

## How It Works

### Session Continuity
```python
# First invocation
response = requests.post("/vu-agent/handle-event", json={
    "sessionId": "abc-123",  # Key to file state
    "prompt": [
        {"role": "user", "content": "Create a Python script that generates a CSV file"}
    ]
})

# Later invocation with same session
response = requests.post("/vu-agent/handle-event", json={
    "sessionId": "abc-123",  # Same session maintains file access
    "prompt": [
        {"role": "user", "content": "Read the CSV file you created and analyze it"}
    ]
})
```

When the agent runs with the same session ID:
1. Previous files are restored to the working directory
2. The agent can access/modify these files
3. Changes are tracked and versioned
4. File state persists after execution

### File Operations Example

```python
# Agent creates a file through code execution
result = execute_python("""
with open('data.csv', 'w') as f:
    f.write('name,age\nAlice,30\nBob,25')
""")

# Files are automatically:
# 1. Detected in working directory
# 2. Uploaded to S3
# 3. Tracked in session index
# 4. Available in response
response = {
    "files": {
        "file-uuid": {
            "original_name": "data.csv",
            "size": 1024,
            "last_modified": "2024-01-20T10:00:00Z",
            "versions": [...]
        }
    }
}
```

### Accessing Files
To download files the agent has created:
```python
# Get download URLs for files
response = requests.post("/vu-agent/get-file-download-urls", json={
    "sessionId": "abc-123",
    "files": ["file-uuid"]
})
```

### Version History
Each file modification creates a new version:
```json
{
    "files": {
        "file-uuid": {
            "original_name": "data.csv",
            "versions": [
                {
                    "version_file_id": "v1-uuid",
                    "timestamp": "2024-01-20T10:00:00Z",
                    "hash": "content-hash-1",
                    "size": 1024
                },
                {
                    "version_file_id": "v2-uuid",
                    "timestamp": "2024-01-20T10:05:00Z",
                    "hash": "content-hash-2",
                    "size": 1025
                }
            ]
        }
    }
}
```

### Key Benefits

1. **Continuity**: The agent can work with files across multiple interactions
   ```python
   # First interaction creates file
   "Create a Python script that writes to output.txt"
   
   # Second interaction modifies it
   "Update the content of output.txt"
   
   # Third interaction reads it
   "What's in output.txt?"
   ```

2. **Versioning**: Every change is tracked
    - Original versions preserved
    - Modification history maintained
    - Changes can be reviewed

3. **State Management**: Files persist beyond Lambda execution
    - Survives container recycling
    - Available across invocations
    - Automatically restored

4. **Security**:
    - Files isolated by session
    - Access controlled through presigned URLs
    - Clean working directory between invocations

This system allows agents to maintain complex file operations across multiple interactions while preserving state and history, all keyed by the session ID.

# Implementation: File State Management

## Core Classes and Methods

### FileTracker Initialization
The `LambdaFileTracker` manages a session's file state through a combination of a temporary working directory and S3 storage:

```python
class LambdaFileTracker:
    def __init__(self, current_user: str, session_id: str, working_dir: str = "/tmp"):
        self.current_user = current_user
        self.session_id = session_id
        self.existing_mappings = {}
        self.initial_state = {}
        self.s3_client = boto3.client('s3')
        self.bucket = os.getenv('AGENT_STATE_BUCKET')
        
        # Create working directory
        self.working_dir = working_dir
        os.makedirs(self.working_dir, exist_ok=True)
```

### Session State Loading
When a session starts, the tracker looks for and restores existing files:

```python
def find_existing_session(self) -> Optional[Dict]:
    index_key = f"{self.current_user}/{self.session_id}/index.json"
    try:
        response = self.s3_client.get_object(
            Bucket=self.bucket,
            Key=index_key
        )
        session_data = json.loads(response['Body'].read().decode('utf-8'))
        self.existing_mappings = session_data.get('mappings', {})
        return session_data
    except ClientError:
        return None

def restore_session_files(self, index_content: Dict) -> bool:
    for original_path, s3_name in index_content['mappings'].items():
        s3_key = f"{self.current_user}/{self.session_id}/{s3_name}"
        local_path = os.path.join(self.working_dir, original_path)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # Download file
        self.s3_client.download_file(self.bucket, s3_key, local_path)
```

### Change Detection
The tracker monitors file changes by comparing current state to initial state:

```python
def get_changed_files(self) -> Tuple[List[str], Dict[str, str], Dict[str, Dict]]:
    current_state = self.scan_directory()
    changed_files = []
    filename_mapping = self.existing_mappings.copy()
    version_info = {}

    for filepath, current_info in current_state.items():
        if (filepath not in self.initial_state or 
            current_info['hash'] != self.initial_state[filepath]['hash']):
            
            # Generate new UUID for changed file
            new_s3_name = str(uuid.uuid4()) + Path(filepath).suffix
            filename_mapping[filepath] = new_s3_name
            
            # Track version info
            version_info[filepath] = {
                's3_name': new_s3_name,
                'timestamp': datetime.utcnow().isoformat(),
                'hash': current_info['hash'],
                'size': current_info['size']
            }
            changed_files.append(filepath)

    return changed_files, filename_mapping, version_info
```

### State Updates
When changes are detected, the tracker updates S3 and the index:

```python
def upload_changed_files(self) -> Dict:
    changed_files, filename_mapping, version_info = self.get_changed_files()
    
    # Get existing index
    index_key = f"{self.current_user}/{self.session_id}/index.json"
    try:
        response = self.s3_client.get_object(Bucket=self.bucket, Key=index_key)
        existing_index = json.loads(response['Body'].read().decode('utf-8'))
    except ClientError:
        existing_index = {
            "user": self.current_user,
            "session_id": self.session_id,
            "mappings": {},
            "version_history": {}
        }

    # Update version history
    for filepath, version_data in version_info.items():
        if filepath not in existing_index['version_history']:
            existing_index['version_history'][filepath] = []
        existing_index['version_history'][filepath].append(version_data)

    # Update mappings and timestamp
    existing_index['mappings'].update(filename_mapping)
    existing_index['timestamp'] = datetime.utcnow().isoformat()

    # Upload index and changed files
    self.s3_client.put_object(
        Bucket=self.bucket,
        Key=index_key,
        Body=json.dumps(existing_index, indent=2)
    )

    for original_path in changed_files:
        s3_key = f"{self.current_user}/{self.session_id}/{filename_mapping[original_path]}"
        local_path = os.path.join(self.working_dir, original_path)
        with open(local_path, 'rb') as file:
            self.s3_client.upload_fileobj(file, self.bucket, s3_key)
```

### File Access
Files are accessed through presigned URLs:

```python
def get_presigned_url_by_id(current_user: str, session_id: str, file_id: str) -> str:
    s3_client = boto3.client('s3')
    index_key = f"{current_user}/{session_id}/index.json"
    
    # Get index content
    response = s3_client.get_object(Bucket=bucket, Key=index_key)
    index_content = json.loads(response['Body'].read().decode('utf-8'))
    
    # Find file in current mappings or version history
    s3_key = None
    for _, s3_filename in index_content['mappings'].items():
        if s3_filename.rsplit('.', 1)[0] == file_id:
            s3_key = f"{current_user}/{session_id}/{s3_filename}"
            break
    
    # Generate URL
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': s3_key},
        ExpiresIn=3600
    )
```

### Cleanup
The tracker ensures cleanup of temporary files:

```python
def __del__(self):
    """Cleanup temporary files when object is destroyed."""
    try:
        if os.path.exists(self.working_dir):
            shutil.rmtree(self.working_dir)
    except Exception as e:
        print(f"Error cleaning up temporary files: {e}")
```

This implementation provides a robust system for managing file state across Lambda invocations while maintaining version history and providing secure access to files.

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

If your assistant includes `ops`, they will automatically be registered by the `handlers.handle_event` function into the ActionRegistry as tools. However, if you need to do this manually, you have a couple of options: 

1. When setting up your agent, you can call `register_op_actions`, which will call the `get_ops` endpoint and fetch the list of ALL ops that exist and register them as tools:

```python
action_registry = PythonActionRegistry()
register_op_actions(action_registry, os.getenv("ACCESS_TOKEN"), os.getenv("CURRENT_USER"))
```

2. When setting up an assistant with specific operations, you can take the json describing the ops (e.g., assistant_ops) and convert them to tools using `ops_to_tools`:

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


## Authentication Flow

For ops to work, you MUST have a valid `access_token` and `current_user` to authenticate the API calls. This is typically done through OAuth or some other authentication mechanism. This information should be passed via the `action_context`:

```python
action_context_props={
"event_handler": event_printer,
"access_token": os.getenv("ACCESS_TOKEN"), # or receive in request via handler
"current_user": os.getenv("CURRENT_USER") # or receive in request via handler
}

    user_input = input("Enter a prompt for the agent: ")

    result = agent.run(user_input=user_input, action_context_props=action_context_props)
```

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
