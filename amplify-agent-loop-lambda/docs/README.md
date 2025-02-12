# Agent Framework Documentation

## Overview
This documentation explains how to build, customize, and deploy agents that can execute tasks through tools, handle email interactions, and maintain state across sessions.

## Core Documentation Files

### [AGENT_FRAMEWORK.md](AGENT_FRAMEWORK.md)
Core implementation details including:
- Agent loop and execution flow
- Tool registration and implementation
- Action context and dependency injection
- Event tracking system
- Memory management
- Agent languages (function calling, JSON, natural language)
- Error handling and retries

### [BUILDING_AGENTS.md](BUILDING_AGENTS.md)
Practical guide covering:
- Creating custom tools with `@register_tool`
- Working with action context and events
- Integrating HTTP operations as tools
- File operations and state management
- Using the LLM for tool responses
- Best practices for tool development
- Registering operations with `register_op_actions`

### [EMAIL_TO_AGENT.md](EMAIL_TO_AGENT.md)
Email integration details including:
- Event template system
- Plus addressing for routing
- Template variables and substitution
- File tracking and versioning
- S3 state management
- DynamoDB metadata
- Session management
- Allowed sender controls

## Getting Started

1. Read [AGENT_FRAMEWORK.md](AGENT_FRAMEWORK.md) to understand the core architecture
2. Follow [BUILDING_AGENTS.md](BUILDING_AGENTS.md) to create your first agent
3. Implement email integration using [EMAIL_TO_AGENT.md](EMAIL_TO_AGENT.md)

## Next Steps
- Create custom tools
- Set up email templates
- Implement file handling
- Add operation integrations

## Contributing
See each document for specific areas where additional documentation is needed or improvements can be made.