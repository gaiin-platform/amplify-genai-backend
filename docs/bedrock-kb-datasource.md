# Bedrock Knowledge Base as Assistant Datasource

## Overview

Add support for Amazon Bedrock Knowledge Base as a datasource type for assistants. Users provide a Bedrock Knowledge Base ID, and the system uses it for retrieval during chat conversations via the Bedrock `Retrieve` and `RetrieveAndGenerate` APIs.

## Current Datasource Architecture

The assistants service supports these datasource types today:

- **File uploads** — stored in S3, chunked/embedded into PostgreSQL via the RAG pipeline
- **Website URLs** — scraped, then processed through the same RAG pipeline
- **Drive integrations** — Google Drive / OneDrive files uploaded via integration APIs
- **Tag-based sources** — prefixed with `tag://`, used for grouping

### Flow: Assistant Creation

1. `create_assistant()` in `amplify-assistants/service/core.py` receives datasources from the request
2. Datasources are categorized by type (website, drive, tag, standard file, bedrock KB)
3. Website URLs are scraped immediately; drive files are uploaded
4. Bedrock KB datasources are validated via `bedrock:GetKnowledgeBase`
5. Standard file datasources go through permission checks and hash translation
6. All datasources are merged and passed to `create_or_update_assistant()`
7. Persisted to DynamoDB `ASSISTANTS_DYNAMODB_TABLE` in the `dataSources` array

### Flow: Chat Retrieval

1. `chat_endpoint()` in `amplify-lambda/chat/service.py` calls `get_data_source_details()` — Bedrock KB sources are passed through without DynamoDB lookup
2. The JS router in `amplify-lambda-js` resolves datasources:
   - `isDocument()` recognizes `bedrock-kb://` as a document type for RAG
   - `translateUserDataSourcesToHashDataSources()` skips hash lookup for Bedrock KB sources
   - `resolveDataSources()` skips permission checks for Bedrock KB sources
3. The `/embedding-dual-retrieval` endpoint separates Bedrock KB sources from standard sources
4. Bedrock KB retrieval runs in parallel with PostgreSQL retrieval
5. Results are merged and returned

### Datasource Data Model

Bedrock KB datasource object:
```json
{
  "id": "bedrock-kb://<knowledge-base-id>",
  "name": "Display name",
  "type": "bedrock/knowledge-base",
  "metadata": {
    "knowledgeBaseId": "<knowledge-base-id>",
    "type": "bedrock/knowledge-base"
  }
}
```

## IAM Permission Changes

### `amplify-assistants` — `AssistantsLambdaPolicy`

**File:** `amplify-assistants/serverless.yml`

```yaml
- Effect: Allow
  Action:
    - bedrock:GetKnowledgeBase
  Resource:
    - "arn:aws:bedrock:${aws:region}:${aws:accountId}:knowledge-base/*"
```

### `embedding` — `EmbeddingIAMPolicy`

**File:** `embedding/serverless.yml`

```yaml
- Effect: Allow
  Action:
    - bedrock:Retrieve
    - bedrock:RetrieveAndGenerate
  Resource:
    - "arn:aws:bedrock:${aws:region}:${aws:accountId}:knowledge-base/*"

- Effect: Allow
  Action:
    - bedrock:ListDataSources
    - bedrock:GetDataSource
  Resource:
    - "arn:aws:bedrock:${aws:region}:${aws:accountId}:knowledge-base/*"

- Effect: Allow
  Action:
    - s3:GetObject
  Resource:
    - "arn:aws:s3:::*/*"
```

- `bedrock:Retrieve` / `bedrock:RetrieveAndGenerate` — used during chat retrieval
- `bedrock:ListDataSources` / `bedrock:GetDataSource` — used by the download endpoint to validate that a requested S3 file belongs to a KB's configured data source bucket
- `s3:GetObject` on `arn:aws:s3:::*/*` — used to generate presigned download URLs for KB source files. Application-level validation (bucket must be linked to the KB) prevents unauthorized access to arbitrary S3 objects.

The existing `bedrock:InvokeModel*` permission on `foundation-model/*` covers the model invocation that `RetrieveAndGenerate` performs internally.

### Cross-Account Note

If a Bedrock Knowledge Base lives in a different AWS account, resource-based policies on the KB side would also be required. The IAM changes above assume same-account access.

## Files Changed

| File | Change |
|------|--------|
| `amplify-assistants/service/core.py` | Categorize and validate Bedrock KB datasources in `create_assistant()`, exclude from permission key collection in `create_or_update_assistant()` |
| `amplify-assistants/serverless.yml` | Add `bedrock:GetKnowledgeBase` IAM permission |
| `amplify-assistants/schemata/create_assistant_schema.py` | Add `type` field to datasource items |
| `embedding/embedding-dual-retrieval.py` | Add Bedrock KB retrieval path alongside PostgreSQL retrieval, handle KB-only and mixed source scenarios |
| `embedding/bedrock-kb-download.py` | New endpoint for secure presigned URL generation for Bedrock KB source files, with bucket validation via `ListDataSources`/`GetDataSource` |
| `embedding/schemata/permissions.py` | Register `/bedrock-kb/download` path and `bedrock-kb-download` op in permission checker |
| `embedding/schemata/schema_validation_rules.py` | Register `/bedrock-kb/download` path in validators and api_validators |
| `embedding/serverless.yml` | Add `bedrock:Retrieve`, `bedrock:RetrieveAndGenerate`, `bedrock:ListDataSources`, `bedrock:GetDataSource`, and `s3:GetObject` IAM permissions; add `bedrock_kb_download` Lambda function |
| `amplify-lambda/chat/service.py` | Pass through Bedrock KB datasources in `get_data_source_details()` without DynamoDB lookup |
| `amplify-lambda-js/datasource/datasources.js` | Recognize Bedrock KB sources in `isDocument()`, skip hash translation and permission checks for `bedrock-kb://` IDs |
| `amplify-lambda-js/common/chat/rag/rag.js` | Preserve full `bedrock-kb://` ID as key (no protocol stripping), skip embedding completion check for KB sources |

## Bedrock KB File Download

### Problem

Bedrock KB retrieval results include S3 URIs (e.g., `s3://kb-data-bucket/documents/guide.pdf`) pointing to the KB's backing data source bucket. Users cannot access these directly.

### Solution

A new endpoint `POST /bedrock-kb/download` validates that the requested S3 file belongs to a bucket configured as a data source for the specified Knowledge Base, then generates a presigned download URL.

### Security Model

1. User sends `knowledgeBaseId` + `s3Uri` to the endpoint
2. The endpoint calls `ListDataSources(knowledgeBaseId)` to get all data source IDs
3. For each data source, calls `GetDataSource` to extract `dataSourceConfiguration.s3Configuration.bucketArn`
4. Validates the requested S3 URI's bucket matches one of the KB's configured buckets
5. Only then generates a presigned URL via `s3:GetObject`

This approach avoids hardcoding bucket names and dynamically validates against the KB's actual configuration. Results are cached per KB ID for the Lambda container lifetime.

### Endpoint

```
POST /bedrock-kb/download
```

Request:
```json
{
  "data": {
    "knowledgeBaseId": "ABCDEFGHIJ",
    "s3Uri": "s3://my-kb-bucket/documents/guide.pdf"
  }
}
```

Success response:
```json
{
  "success": true,
  "downloadUrl": "https://my-kb-bucket.s3.amazonaws.com/documents/guide.pdf?...",
  "fileName": "guide.pdf",
  "message": "Download URL generated successfully"
}
```

Error responses:
```json
{"success": false, "message": "S3 bucket 'wrong-bucket' is not a configured data source for KB ABCDEFGHIJ"}
{"success": false, "message": "File not found: s3://my-kb-bucket/documents/missing.pdf"}
```
