# Bedrock Knowledge Base — Frontend Integration Guide

## Summary

The backend now supports Bedrock Knowledge Base as a datasource type for assistants. Users provide a Knowledge Base ID, the backend validates it exists, stores it as a datasource, and queries it during chat. This document covers everything needed to update the UI.

## New Datasource Type

Type identifier: `bedrock/knowledge-base`
ID format: `bedrock-kb://<knowledge-base-id>`

A Bedrock KB datasource object looks like:

```json
{
  "id": "bedrock-kb://ABCDEFGHIJ",
  "name": "My Knowledge Base",
  "type": "bedrock/knowledge-base",
  "metadata": {
    "knowledgeBaseId": "ABCDEFGHIJ",
    "type": "bedrock/knowledge-base"
  }
}
```

The `knowledgeBaseId` is a 10-character alphanumeric string assigned by AWS Bedrock (e.g., `ABCDEFGHIJ`). Users get this from the AWS Bedrock console.

## API Changes

### POST `/assistant/create` — Create/Update Assistant

The `dataSources` array now accepts Bedrock KB objects alongside existing file/website/drive datasources.

Request payload (only the new datasource shown):

```json
{
  "data": {
    "name": "My Assistant",
    "description": "Uses a Bedrock Knowledge Base",
    "tags": ["knowledge-base"],
    "instructions": "Answer questions using the knowledge base.",
    "disclaimer": "",
    "dataSources": [
      {
        "id": "bedrock-kb://ABCDEFGHIJ",
        "name": "Product Documentation KB",
        "type": "bedrock/knowledge-base",
        "metadata": {
          "knowledgeBaseId": "ABCDEFGHIJ",
          "type": "bedrock/knowledge-base"
        }
      }
    ]
  }
}
```

The backend will:
1. Validate the KB ID exists via AWS API
2. Return an error if the KB is not found: `{"success": false, "message": "Bedrock Knowledge Base not found: ABCDEFGHIJ"}`
3. On success, store it in the assistant's `dataSources` array

The response shape is unchanged:

```json
{
  "success": true,
  "message": "Assistant created successfully",
  "data": {
    "assistantId": "astp/...",
    "id": "ast/...",
    "version": 1,
    "data_sources": [...]
  }
}
```

### Chat — No Changes Required

Bedrock KB datasources are stored in the assistant's `dataSources` array and flow through the existing chat pipeline automatically. The backend handles separating them from standard datasources and querying the Bedrock KB during retrieval. No chat payload changes are needed from the frontend.

## UI Requirements

### Assistant Creation/Edit Form

Add a new datasource input option for Bedrock Knowledge Base. The user needs to provide:

| Field | Required | Description |
|-------|----------|-------------|
| Knowledge Base ID | Yes | The AWS Bedrock Knowledge Base ID (10-char alphanumeric) |
| Display Name | No | A friendly name for the KB (shown in the datasource list) |

Suggested UX:
- Add a "Bedrock Knowledge Base" option to the datasource type selector (alongside file upload, website URL, drive integration)
- When selected, show a text input for the Knowledge Base ID
- Optional: a text input for a display name (defaults to the KB ID if not provided)
- The KB ID input should accept a string like `ABCDEFGHIJ` — the frontend should construct the full datasource object with the `bedrock-kb://` prefix

### Constructing the Datasource Object

When the user adds a Bedrock KB datasource, build the object like this:

```javascript
function createBedrockKbDatasource(knowledgeBaseId, displayName) {
  return {
    id: `bedrock-kb://${knowledgeBaseId}`,
    name: displayName || knowledgeBaseId,
    type: "bedrock/knowledge-base",
    metadata: {
      knowledgeBaseId: knowledgeBaseId,
      type: "bedrock/knowledge-base",
    },
  };
}
```

This object gets added to the `dataSources` array in the create/update request alongside any other datasources.

### Datasource List Display

When rendering an assistant's datasources, detect Bedrock KB entries by:
- `type === "bedrock/knowledge-base"`, or
- `id` starts with `"bedrock-kb://"`

Display them differently from file datasources:
- Show a distinct icon (e.g., a database or cloud icon)
- Show the display name and KB ID
- No file size, token count, or upload date — these don't apply
- No download action — there's no file to download

### Editing an Existing Assistant

When loading an existing assistant for editing, Bedrock KB datasources will appear in the `dataSources` array from the GET response. Parse them back into the form:

```javascript
function isBedrockKbDatasource(ds) {
  return ds.type === "bedrock/knowledge-base" || ds.id?.startsWith("bedrock-kb://");
}

function extractKbId(ds) {
  return ds.metadata?.knowledgeBaseId || ds.id?.replace("bedrock-kb://", "");
}
```

### Validation

Client-side validation before submitting:
- KB ID should be non-empty
- KB ID should be alphanumeric (Bedrock KB IDs are typically 10 uppercase alphanumeric characters)
- Duplicate KB IDs in the same assistant should be prevented

The backend performs the authoritative validation (checking the KB actually exists), so the frontend should handle the error response gracefully:

```javascript
// Example error handling
if (!response.success && response.message.includes("Knowledge Base not found")) {
  // Show error on the KB ID input: "Knowledge Base not found. Check the ID and try again."
}
```

### Removing a Bedrock KB Datasource

Same as removing any other datasource — just filter it out of the `dataSources` array before submitting the update.

## Error States

| Error | When | Suggested UI |
|-------|------|-------------|
| `Bedrock Knowledge Base not found: <id>` | KB ID doesn't exist in AWS | Inline error on the KB ID input field |
| `Error validating Knowledge Base: <details>` | AWS API error (permissions, network) | Toast/banner: "Unable to validate Knowledge Base. Try again later." |

## Mixing Datasource Types

An assistant can have Bedrock KB datasources alongside file uploads, website URLs, and drive integrations. They all go in the same `dataSources` array. The backend handles routing queries to the right retrieval system at chat time.

## Source Citations for Bedrock KB Results

Bedrock KB retrieval results include source location metadata from the KB's backing S3 bucket (e.g., `s3://kb-data-bucket/documents/guide.pdf`). These are not directly accessible to users.

Each RAG source object sent to the frontend for Bedrock KB results will have:

```json
{
  "key": "bedrock-kb://ABCDEFGHIJ",
  "contentKey": "bedrock-kb://ABCDEFGHIJ",
  "name": "Product Documentation KB",
  "type": "bedrock/knowledge-base",
  "url": null,
  "locations": [{"source": "s3://kb-data-bucket/documents/guide.pdf"}],
  "content": "The retrieved text content..."
}
```

### Display Recommendations

- Show the source file name extracted from the S3 URI (e.g., `guide.pdf` from `s3://kb-data-bucket/documents/guide.pdf`)
- If the `locations` array contains S3 URIs, render a download link that calls the new download endpoint
- If download fails, fall back to showing the file name without a link

### Downloading Source Files

A new backend endpoint generates presigned download URLs for Bedrock KB source files. The backend validates that the requested file belongs to a bucket configured as a data source for the KB before generating the URL.

```
POST {API_BASE_URL}/bedrock-kb/download
```

Request:
```json
{
  "data": {
    "knowledgeBaseId": "ABCDEFGHIJ",
    "s3Uri": "s3://kb-data-bucket/documents/guide.pdf"
  }
}
```

Success response:
```json
{
  "success": true,
  "downloadUrl": "https://kb-data-bucket.s3.amazonaws.com/documents/guide.pdf?X-Amz-...",
  "fileName": "guide.pdf",
  "message": "Download URL generated successfully"
}
```

Error responses:
```json
{"success": false, "message": "S3 bucket 'wrong-bucket' is not a configured data source for KB ABCDEFGHIJ"}
{"success": false, "message": "File not found: s3://kb-data-bucket/documents/missing.pdf"}
```

Example frontend helper:

```javascript
async function downloadBedrockKbFile(accessToken, knowledgeBaseId, s3Uri) {
  const response = await fetch(`${API_BASE_URL}/bedrock-kb/download`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      data: { knowledgeBaseId, s3Uri },
    }),
  });

  const result = await response.json();
  if (result.success && result.downloadUrl) {
    // Open the presigned URL in a new tab or trigger download
    window.open(result.downloadUrl, "_blank");
  } else {
    console.error("Download failed:", result.message);
  }
}
```

To extract the KB ID and S3 URI from a RAG source citation:

```javascript
function getDownloadInfoFromSource(source) {
  if (!isBedrockKbDatasource(source)) return null;

  const kbId = source.contentKey?.replace("bedrock-kb://", "") || extractKbId(source);
  const s3Uri = source.locations?.[0]?.source;

  if (!kbId || !s3Uri || !s3Uri.startsWith("s3://")) return null;

  return { knowledgeBaseId: kbId, s3Uri };
}
```
