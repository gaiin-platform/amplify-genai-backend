# Datasource API

This document describes the API for retrieving datasource content directly via HTTP.

## Overview

The Datasource API allows clients to request content from datasources via HTTP without requiring a full chat session. This is useful for retrieving document content, previewing files, or obtaining download URLs for resources.

## API Endpoint

The datasource endpoint uses the same base URL as the chat API. To make a datasource request, include the `datasourceRequest` field in your request body.

## Request Format

```json
{
  "datasourceRequest": {
    "dataSources": [
      {
        "id": "s3://user123/document1.pdf",
        "type": "application/pdf"
      },
      {
        "id": "tag://important-docs",
        "type": "application/json"
      }
    ],
    "options": {
      "useSignedUrls": true
    },
    "chat": {
      "messages": [
        {
          "role": "user",
          "content": "Optional message for context - can be empty array if just fetching content"
        }
      ]
    }
  }
}
```

### Fields:

- `datasourceRequest`: Root object for the datasource request
  - `dataSources`: Array of datasource objects to retrieve
    - `id`: Identifier for the datasource (s3://, tag://, etc.)
    - `type`: MIME type of the datasource
  - `options`: (Optional) Configuration options
    - `useSignedUrls`: Boolean flag to return signed URLs for S3 content instead of the actual content
  - `chat`: (Optional) Chat context information
    - `messages`: Array of messages that provide context for datasource resolution

## Response Format

```json
{
  "statusCode": 200,
  "body": {
    "dataSources": [
      {
        "id": "s3://user123/document1.pdf",
        "type": "application/pdf",
        "content": {
          "name": "document1.pdf",
          "content": [
            {
              "content": "Document content here...",
              "location": { "page": 1 }
            }
          ]
        },
        "format": "content"
      },
      {
        "id": "s3://user123/image.jpg",
        "type": "image/jpeg",
        "ref": "https://presigned-url-to-s3-resource.com/...",
        "format": "signedUrl"
      }
    ]
  }
}
```

### Fields:

- `statusCode`: HTTP status code for the response
- `body`: Response body
  - `dataSources`: Array of datasource results
    - `id`: Identifier for the datasource
    - `type`: MIME type of the datasource
    - `content`: Actual content of the datasource (if format is "content")
    - `ref`: Reference URL for the datasource (if format is "signedUrl")
    - `format`: Type of response - one of: "content", "signedUrl", "error"
    - `error`: Error message (only present if format is "error")

## Error Responses

### Authentication Error

```json
{
  "statusCode": 401,
  "body": {
    "error": "Unauthorized"
  }
}
```

### Bad Request

```json
{
  "statusCode": 400,
  "body": {
    "error": "No data sources provided"
  }
}
```

### Access Denied

```json
{
  "statusCode": 401,
  "body": {
    "error": "Unauthorized data source access."
  }
}
```

## Example Usage

### Request document content directly

```json
{
  "datasourceRequest": {
    "dataSources": [
      {
        "id": "s3://user123/document1.pdf",
        "type": "application/pdf"
      }
    ],
    "chat": {
      "messages": []
    }
  }
}
```

### Request document content via tag with signed URLs

```json
{
  "datasourceRequest": {
    "dataSources": [
      {
        "id": "tag://quarterly-reports",
        "type": "application/pdf"
      }
    ],
    "options": {
      "useSignedUrls": true
    },
    "chat": {
      "messages": []
    }
  }
}
```