# Release Notes: v0.9.0

**Release Date**: February 2026
**Previous Version**: v0.8.1 (December 2, 2025)
**Branch**: main

---

## Summary

Version 0.9.0 is a major platform update delivering significant architectural changes, new features, and infrastructure modernization. With **162 commits**, **358 files changed**, and **6 merged pull requests**, this release represents the largest evolution of the Amplify backend since launch.

### Highlights at a Glance
- :warning: **Breaking Change** — All shared environment variables migrated to AWS Parameter Store
- :warning: **Basic-Ops Elimination** — `amplify-lambda-basic-ops` service removed; existing deployments must migrate
- :rocket: **21 New AI Models** — GPT-5 series, Claude 4.x, Gemini 2.x, Amazon Nova, Mistral Pixtral
- :zap: **API Gateway Streaming** — Migrated from Function URLs to API Gateway Lambda response streaming
- :mag: **Web Search** — Admin-configurable web search integration
- :bell: **Critical Error Tracking** — Centralized error monitoring with email notifications
- :bar_chart: **Configurable Embedding Dimensions** — Support for Nova Multimodal Embeddings and flexible vector sizes

---

## :warning: Breaking Changes & Migration

### AWS Parameter Store Migration

All shared environment variables have been migrated to AWS Parameter Store. Existing deployments **must** run the population script before deploying any services:

```bash
python3 scripts/populate_parameter_store.py --stage <stage> --dep-name <dep-name>
```

Services will fail to deploy without this step. See the [Migration Guide](./scripts/MIGRATION_README.md) for complete instructions.

### Basic-Ops Service Elimination

The `amplify-lambda-basic-ops` service has been removed. Its `/user-data` functionality is now handled by `amplify-lambda`. **If you have the `amplify-lambda-basic-ops` CloudFormation stack deployed**, you must:

1. Check if the stack exists: `aws cloudformation describe-stacks --stack-name amplify-<dep-name>-basic-ops-<stage>`
2. If it exists, check for user storage data and handle the `/user-data` API Gateway endpoint conflict
3. Follow [Migration Guide Step 3a](./scripts/MIGRATION_README.md) for detailed instructions

### Coordinated Deployment Required

The backend and frontend must be deployed together — **deploy the backend first**, then rebuild and deploy the frontend container.

---

## :new: What's New

### Model Updates

**21 new models added**, 1 updated, 3 removed (net +18):

**Amazon Bedrock:**
- `amazon.nova-2-multimodal-embeddings-v1:0` — Nova Multimodal Embeddings
- `amazon.nova-lite-v1:0`, `amazon.nova-micro-v1:0`, `amazon.nova-pro-v1:0` — Nova family

**Google Gemini:**
- `gemini-2.0-flash` — Second-generation workhorse (1M context)
- `gemini-2.5-flash` — Best price-performance with thinking features (1M context, 65K output)
- `gemini-2.5-pro` — Promoted from preview to stable release

**OpenAI GPT:**
- `gpt-4.1-mini` — Latest efficient model
- `gpt-5`, `gpt-5-mini`, `gpt-5.1`, `gpt-5.2` — Frontier reasoning and coding models

**OpenAI Reasoning:**
- `o3` — Advanced reasoning
- `o4-mini` — Cost-effective compact reasoning

**Anthropic Claude (Bedrock):**
- `us.anthropic.claude-3-5-haiku-20241022-v1:0` — Claude 3.5 Haiku
- `us.anthropic.claude-opus-4-20250514-v1:0` — Claude 4 Opus
- `us.anthropic.claude-opus-4-5-20251101-v1:0` — Claude Opus 4.5
- `us.anthropic.claude-sonnet-4-20250514-v1:0` — Claude 4 Sonnet

**Mistral:**
- `us.mistral.pixtral-large-2502-v1:0` — Pixtral Large (124B multimodal)

**Removed:** `amazon.titan-embed-text-v1`, `text-embedding-3-large`, `text-embedding-3-small` (replaced by Nova embeddings)

*Pull Request: #275*

---

### API Gateway Lambda Response Streaming

Migrated from Function URLs to API Gateway Lambda response streaming:
- API Gateway streaming format with metadata JSON and 8-byte delimiter
- Changed integration type from `AWS` to `AWS_PROXY` for streaming support
- Updated Lambda invocation URI to use `/2021-11-15/response-streaming-invocations` endpoint

*Pull Request: #275*

---

### Web Search Functionality

Admin-configurable web search integration:
- Feature flag support for enabling/disabling web search
- Migrated web search admin configuration to unified admin service
- New `webSearch.js` module for search operations
- Frontend preferences handling in assistants
- Fixed `web_search_preview` tool triggering with images
- Integration with OpenAI `/v1/responses` endpoint

*Pull Request: #275*

---

### Critical Error Tracking & Monitoring

Centralized error monitoring infrastructure:
- Critical error tracker service with database schema
- Critical error processor for event handling
- Email notification system (`critical_error_notifier.py`)
- Integration across multiple Lambda services
- New `criticalLogger.js` for JavaScript services
- Schema validation and permissions for `critical_errors` endpoint

*Pull Request: #275*


---

### Additional Charges & Billing

- Support for additional charges tracking and billing
- Custom cost attribution
- Enhanced billing capabilities

*Pull Request: #275*

---

## :wrench: Embeddings & Vector Database

### Nova Multimodal Embeddings Support
- Amazon Bedrock Nova Multimodal Embeddings model support
- Updated embedding code for Nova MME compatibility
- Comprehensive documentation (`EMBEDDING_DIMENSIONS.md`, `MODEL_CONFIGURATION.md`)

*Pull Requests: #264, #268*

### Configurable Embedding Dimensions
- `EMBEDDING_DIM` environment variable (defaults to 1536)
- Updated `create_table.py` for configurable vector dimensions
- `populate_parameter_store` script updated with `EMBEDDING_DIM` format
- Backward compatible: existing deployments unaffected (`CREATE TABLE IF NOT EXISTS`)
- Cross-provider compatibility (Amazon Nova, Cohere Embed v3, OpenAI)

*Pull Request: #264*

### Embedding Improvements
- Enhanced embedding-dual-retrieval with better query handling
- Dead Letter Queue (DLQ) processing for failed embedding chunks (`embedding-dlq-handler.py`)
- Improved Excel file handling for corrupted files
- Better error handling in visual transcription
- Optimized database interactions
- Enhanced logging for embedding diagnostics

*Pull Requests: #262, #275*

---

## :closed_lock_with_key: OAuth & Integrations

### Microsoft Azure Admin Consent
- New admin setting for Microsoft Azure integrations
- Store consent settings in DynamoDB alongside existing integrations data
- Check consent setting when user connects an integration

*Pull Request: #266*

### OAuth Enhancements
- Dynamic redirect URI support
- Improved error handling and retry logic for consent errors
- Better origin detection from event headers
- Calendar time zone bug fixes

*Pull Request: #275*

---

## :moneybag: Billing & Cost Tracking

### Usage Tracking
- New centralized `usageTracking.js` module
- Fixed null check for `CachedCostPerThousandTokens` in `recordUsage`
- Support for OpenAI cached tokens in usage tracking
- Handle `response.completed` usage format
- New `accounting.js` for shared accounting logic

*Pull Request: #265*

### Model Rate Table Updates
- Added pricing for 21 new models
- Updated `model_rate_values.csv`
- Fixed Bedrock cost tracking bugs
- Improved MTD cost calculations

*Pull Requests: #265, #275*

---

## :bug: Bug Fixes

- **Bedrock validation errors**: Convert tool roles to user, handle null `toolUseId`, disable reasoning with tools when incompatible (#265)
- **OpenAI usage format**: Handle `response.completed` usage format and cached tokens in `openaiUsageTransform` (#265)
- **Visual transcription**: Improved error handling and model retrieval logic (#262)
- **IAM policy deployment ordering**: Fixed race condition with hardcoded ARN → `!Ref` for implicit CloudFormation dependencies
- **Lambda size limits**: Added `slimPatterns` to strip 85MB Google API discovery cache from assistants-api layer

---

## :robot: Assistants & Agent Framework

- Refactored assistant API serverless configuration to use Python requirements layer
- Enhanced user-defined assistants with better state management
- Improved code interpreter integration
- Added OpenAI provider support to agent framework
- Enhanced agent prompt handling and core logic
- Improved scheduled tasks processing
- Removed legacy Docker build files and deployment scripts

*Pull Request: #275*

---

## :gear: Infrastructure & DevOps

### Parameter Store Integration
- All shared environment variables resolved from AWS SSM Parameter Store
- `populate_parameter_store.py` script for initial population
- Comprehensive migration guide (`MIGRATION_README.md`)

### Var Template Updates
- Updated `<env>-var.yml.template` with new embedding variables and comments
- All dimensions remain default 1536 unless overridden

*Pull Request: #268*

### Standalone Service Deployment
The following services are deployed independently (not included in `serverless-compose.yml`):
- `amplify-agent-loop-lambda/` — Agent Loop
- `amplify-lambda-assistants-api/` — Assistants API
- `amplify-lambda-assistants-api-office365/` — Office 365 Integration
- `amplify-lambda-assistants-api-google/` — Google Workspace Integration

---

## :chart_with_upwards_trend: Stats

| Metric | Count |
|--------|-------|
| Commits | 162 |
| Files changed | 358 |
| Insertions | +76,919 |
| Deletions | -49,986 |
| Pull Requests | 6 |
| New AI Models | 21 |

---

## :busts_in_silhouette: Contributors

- **Karely Rodriguez** (@karelyrodri) — Lead developer, core infrastructure, JIT provisioning, streaming
- **Allen Karns** (@karnsab) — Release management, deployment fixes, parameter store migration
- **Jason Bradley** (@jasonbrd) — Embedding dimensions, Nova MME, model updates, var template
- **Seviert** (@seviert23) — Microsoft Azure admin consent integration

---

## Deployment Instructions

1. Run `populate_parameter_store.py` (required for all deployments)
2. Handle basic-ops migration if applicable (see [Migration Guide Step 3a](./scripts/MIGRATION_README.md))
3. Deploy core services: `serverless deploy --stage <env>`
4. Deploy standalone services as needed (see Step 5a in [deployment docs](https://github.com/gaiin-platform/.github/blob/main/profile/README.md))
5. Rebuild and deploy the frontend container

See the full [Migration Guide](./scripts/MIGRATION_README.md) for detailed instructions.
