# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Building and Deployment
- `./build.sh [stage]` - Build Docker container image for Lambda deployment (default stage: dev)
- `./build-fat.sh [stage]` - Build fat container with all dependencies
- `./deploy.sh [stage] [region]` - Build and deploy to AWS Lambda (default: dev stage, configured AWS region)
- `./deploy-cached.sh [stage]` - Deploy using cached build
- `./generate-linux-lock.sh` - Generate Linux-compatible Poetry lock file

### Testing
- `pytest` - Run tests using the pytest framework (configured in pyproject.toml)
- Tests should be placed in test files, though no test directory structure currently exists

### Local Development
- Use `poetry` for dependency management (pyproject.toml defines all dependencies)
- Python 3.11 is required for compatibility with AWS Lambda runtime
- Use `serverless offline` for local testing of Lambda functions

## Architecture Overview

### Core Components

**Agent Framework (`agent/`):**
- `agent/core.py` - Core agent loop, memory management, and action execution
- `agent/agents/` - Specialized agent types (actions, summarizer, workflow)
- `agent/capabilities/` - Workflow capabilities and models
- `agent/components/` - Agent registry, tools, languages, and utilities
- `agent/tools/` - Extensive tool library (file handling, HTTP, database, shell, etc.)

**Service Layer (`service/`):**
- `service/core.py` - Main Lambda entry point and request routing
- `service/handlers.py` - Request handlers for various operations
- `service/routes.py` - API routing configuration
- `service/conversations.py` - Conversation state management
- `service/workflow_handlers.py` - Workflow-specific handlers
- `service/email_events_handlers.py` - Email integration handlers

**Infrastructure:**
- `serverless.yml` - AWS Lambda and infrastructure configuration
- Multiple Lambda functions: agentRouter, agentEventProcessor, scheduledTasksProcessor
- Container-based deployment using ECR
- DynamoDB tables for state management
- SQS queues for agent event processing
- S3 buckets for file storage and templates

### Key Features

**Agent System:**
- Tool-based architecture with `@register_tool` decorator
- Multiple agent languages (function calling, JSON, natural language)
- Memory management and conversation history
- Event tracking and state persistence
- Action context with dependency injection

**Email Integration:**
- Event template system for email-to-agent workflows
- Plus addressing for routing (`+tag` support)
- Template variables and substitution
- File tracking and S3 state management

**Workflow System:**
- Workflow templates stored in DynamoDB and S3
- Scheduled task execution every 3 minutes
- Template registry and versioning

**Data Processing:**
- Comprehensive data science stack (pandas, numpy, scikit-learn, etc.)
- Document processing (PDF, Word, Excel, etc.)
- Image and audio processing capabilities
- Database connectivity tools

### Lambda Functions

1. **agentRouter** - Main API handler for agent requests
2. **agentRouterContainer/Fat** - Container-based variants with different dependency sets
3. **agentEventProcessor** - Processes SQS queue events
4. **scheduledTasksProcessor** - Runs scheduled tasks every 3 minutes
5. **toolsEndpointLambda** - Returns available builtin tools

### Tool Development

Tools are registered using the `@register_tool` decorator and can access:
- Action context for request metadata
- LLM for processing responses
- File operations and state management
- HTTP operations integration
- Database connections

See `docs/BUILDING_AGENTS.md` for detailed tool development guide.

### Environment Variables

The system uses extensive environment configuration for:
- DynamoDB table names
- S3 bucket names
- AWS service ARNs
- OAuth configuration
- Model endpoints and secrets

### Deployment

Deployment uses Docker containers pushed to ECR with timestamped tags. The system supports multiple stages (dev, staging, prod) with stage-specific configurations loaded from `../var/${stage}-var.yml`.