# Changelog

All notable changes to the Amplify GenAI Backend between version 0.8.0 and the current main branch.

### 🎉 Summary

This release includes **270 commits** with significant enhancements to integrations, performance, cost tracking, migration tooling, and the JavaScript/Node.js refactor. Major improvements include SharePoint integration, OpenAI agent support, Bedrock cost tracking fixes, and comprehensive ID migration capabilities.

---

## 🚀 Major Features

### SharePoint Integration
- **Full SharePoint Integration** with navigation and folder support (#259, #260)
  - Add SharePoint file browsing and access
  - Fix Graph API pagination handling
  - Update mimeType formatting for folders
  - Async list group enhancement for better performance

### Agent Framework Enhancements
- **OpenAI Provider Support** for agent framework (#257)
  - Add OpenAI as LLM provider option for agents

### Office 365 Integration Improvements
- **Excel Integration Fixes** (#238)
  - Use worksheet_name instead of worksheet_id for consistency
  - Improve function parameter handling
- **OneNote Integration Updates** (#238)
  - Enhanced OneNote page creation capabilities
  - Note: Image embedding still in development

### Google Calendar Integration
- **Time Zone Bug Fix** (#253)
  - Fixed critical time zone handling in calendar integrations
  - Improved date/time processing accuracy

### Database Connection Management
- **Database Tool Enhancements**
  - Query database functionality with Vanna AI integration
  - Support for multiple database types: PostgreSQL, MySQL, SQL Server, DuckDB, Snowflake, BigQuery, Oracle
  - Secure credential management with password masking
  - Connection testing and validation
  - Query generation using natural language

### Email Webhook System
- **Microsoft Graph Email Webhooks** (Phase 1)
  - Direct SQS forwarding architecture
  - Azure AD User GUID management
  - Local development compatibility
  - Webhook subscription management

---

## 🐛 Critical Bug Fixes

### Cost Tracking
- **Fix Critical Bedrock Cost Tracking Bugs** (#258)
  - Resolve cost calculation errors for AWS Bedrock models
  - Improve usage tracking accuracy
  - Fix token counting for cached inputs

### Document Processing
- **Prevent Unintended RAG Deletion** (#254)
  - Add safeguards to prevent accidental RAG data deletion
  - Improve RAG lifecycle management

### Image Processing
- **Fix OpenAI Image Format Issues** (#252)
  - Use input_text/input_image types for non-standard endpoints
  - Fix image_url format (flat string instead of nested object)
  - Prevent web_search from triggering on image-only queries

### Pagination & Performance
- **Optimize User Cost Data Auto-Loading** (#255)
  - Increase pagination limits for large datasets
  - Optimize DynamoDB query batching

---

## 🎨 API & Schema Enhancements

### New Endpoints
- **Cost History**
  - `getUserCostHistory` - Monthly cost aggregation endpoint
  - Support for multi-account users
  - Historical trends and summaries
- **File Operations**
  - Dynamic filtering capabilities for file queries (#210)
  - Embedding status checking (asynchronous)
  - Extract sitemap URLs without scraping (#208)

---

## 🔐 Security & Access Control

### Permission Updates
- **Enhanced Permission System**
  - Standalone assistant data source permissions
  - Drive data source integration
  - DynamoDB scan permissions for groups table
  - SQS permissions for conversation analysis

### OAuth & Integration Security
- **Dynamic Redirect URI Handling**
  - Origin detection from event headers
  - Retry logic for Microsoft OAuth consent errors
  - Enhanced error management
  - Secure credential serialization

---

## 📦 Embedding & RAG Improvements

### Document Reprocessing
- **Selective Reprocessing** (#226)
  - Handle incomplete embeddings intelligently
  - Delete specific chunks for cleanup efficiency
  - Progress tracking for embedding operations
  - Enhanced error handling

### Data Source Management
- **Improved RAG Processing**
  - Better data source filtering
  - Image source separation
  - Critical context handling logging

### Website Data Sources
- **Sitemap Extraction** (#208)
  - Extract URLs from sitemaps without scraping
  - Support for maxPages parameter
  - URL filtering and exclusions
  - Improved scraping logic

---

## 🛠️ DevOps & Configuration

### CloudFormation Updates
- **Change Set Management** (#255)
  - Update CloudFormation change set plugin
  - Add CHANGE_SET_BOOLEAN environment variable
  - Improve deployment safety

### Serverless Configuration
- **Timeout Optimizations**
  - Reduce reset_billing timeout (15m → 3m)
  - Fix SQS batching window configuration
  - Adjust visibility timeout settings
  - Optimize function memory allocation

---

## 🧹 Cleanup & Refactoring

### Code Cleanup
- **Remove Unused Code**
  - Delete obsolete flow files
  - Remove deprecated DynamoDB table references
  - Clean up unused imports across services
  - Remove chat_usage_archive.py and related scripts

### Dependency Updates
- **Package Version Updates**
  - PyCommon v0.1.0 → v0.1.1
  - OpenAI package 1.99.5
  - Anthropic models refresh (remove deprecated)
  - Database libraries: psycopg2, pymysql, pyodbc, etc.

### Table Name Standardization
- **DynamoDB Naming Consistency**
  - Update tables to use 'js' suffix where appropriate
  - Use 'lambda' suffix for Python services
  - Remove GROUP_ASSISTANT_DASHBOARDS_DYNAMO_TABLE
  - Standardize across all 15 services


### Key Contributors
- Karely Rodriguez (@karely)
- Allen Karns (@allenkarns)
- Jason Bradley (@jasonbrd)
- Max Moundas (@maxmoundas)
- Sam Hays (@samhays)
- Andrew Walker (@FortyAU-Amplify-Team)
- Seviert (@seviert23)

---

## 📅 Timeline Highlights

- **Dec 2, 2025**: SharePoint integration and async list group enhancement
- **Dec 1, 2025**: SharePoint mimeType formatting updates
- **Nov 26, 2025**: Initial SharePoint integration with Graph API pagination
- **Nov 24, 2025**: Critical Bedrock cost tracking fixes
- **Nov 23, 2025**: OpenAI provider support for agent framework
- **Nov 21, 2025**: CloudFormation change set updates and pagination optimization
- **Nov 20, 2025**: Calendar time zone bug fix
- **Nov 19, 2025**: OpenAI image format fixes
- **Nov 14, 2025**: Cognito sub migration support
- **Nov 13, 2025**: User cost history API endpoint
- **Nov 10, 2025**: OAuth integration improvements
- **Nov 7, 2025**: Data disclosure migration and markitdown integration
- **Oct 31, 2025**: User-defined assistant RAG processing enhancements
- **Oct 29-30, 2025**: Major LiteLLM to UnifiedLLM refactor
- **Oct 23-24, 2025**: Circuit breaker and structured logging implementation
- **Oct 21-22, 2025**: JIT provisioning implementation
- **Sep-Oct, 2025**: ID migration script development and testing
- **Sep 13, 2025**: Document reprocessing and embedding improvements
- **Aug 28, 2025**: Dynamic file query filtering
- **Aug 25, 2025**: Stage prefix for admin table stream ARN
- **Aug 22, 2025**: Database connection management features

---


## 📦 Deployment Notes

### Environment Variables
Review `dev-var.yml-example` for new required variables:
- `CHANGE_SET_BOOLEAN` - CloudFormation change set control
- `LOG_LEVEL` - Logging verbosity control
- `AGENT_STATE_DYNAMODB_TABLE` - Agent state management
- Database connection variables (if using DB features)



