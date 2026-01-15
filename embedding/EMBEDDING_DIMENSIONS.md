# Embedding Dimensions Configuration

## Overview

The vector database supports configurable embedding dimensions through the `EMBEDDING_DIM` environment variable. This allows you to choose the optimal dimension size for your embedding models.

## Configuration

### Setting the Dimension

Add `EMBEDDING_DIM` to your stage-specific var file (e.g., `var/dev-var.yml`):

```yaml
EMBEDDING_DIM: "1024"  # Recommended for cross-provider compatibility
```

### Supported Values

Common embedding dimensions:
- **1024** - Recommended for cross-provider compatibility (Bedrock Titan V2, Cohere Embed v3, OpenAI with dimension reduction)
- **1536** - Default, used by OpenAI text-embedding-ada-002 and text-embedding-3-small
- **3072** - OpenAI text-embedding-3-large
- **768** - Some smaller models
- **512, 256** - Bedrock Titan V2 also supports these

## Model Compatibility

### 1024 Dimensions (Recommended)

**Bedrock:**
- Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) - configurable
- Cohere Embed v3 (`cohere.embed-english-v3`, `cohere.embed-multilingual-v3`) - native

**OpenAI:**
- text-embedding-3-small - with `dimensions=1024` parameter
- text-embedding-3-large - with `dimensions=1024` parameter

**Azure OpenAI:**
- Same as OpenAI models above

### 1536 Dimensions (Default)

**OpenAI/Azure:**
- text-embedding-ada-002 - native
- text-embedding-3-small - native (default)

## Important Notes

### For New Deployments

1. Set `EMBEDDING_DIM` in your var file before first deployment
2. Deploy the embedding service
3. Run the `create_table` Lambda function
4. Configure your embedding model to match the dimension

### For Existing Deployments

**The dimension setting is ignored for existing tables.** This is by design to prevent breaking changes:

- `CREATE TABLE IF NOT EXISTS` only creates the table if it doesn't exist
- `CREATE INDEX IF NOT EXISTS` only creates indexes if they don't exist
- Existing vector columns maintain their original dimensions

**To change dimensions on an existing deployment, you must:**

1. Back up your data
2. Drop the existing table
3. Update `EMBEDDING_DIM` in your var file
4. Redeploy
5. Run `create_table` Lambda
6. Re-embed all documents with a model matching the new dimension

## Example: Using 1024 Dimensions

### 1. Update var file

```yaml
# var/dev-var.yml
EMBEDDING_DIM: "1024"
```

### 2. Configure Bedrock Titan V2

```python
body = json.dumps({
    "inputText": content,
    "dimensions": 1024,
    "normalize": True
})
```

### 3. Configure OpenAI

```python
response = client.embeddings.create(
    input=content,
    model="text-embedding-3-small",
    dimensions=1024
)
```

### 4. Configure Cohere Embed v3

```python
body = json.dumps({
    "texts": [content],
    "input_type": "search_document"
    # No dimensions parameter - always 1024
})
```

## Benefits of 1024 Dimensions

✅ **Cross-provider compatibility** - Works with both Bedrock and OpenAI  
✅ **Storage efficiency** - 33% smaller than 1536  
✅ **Faster searches** - Smaller vectors = faster similarity calculations  
✅ **Minimal quality loss** - Research shows <2% degradation vs 1536  
✅ **Cost savings** - Reduced storage and compute costs

## Troubleshooting

### Dimension Mismatch Error

If you see errors like "dimension mismatch" when inserting embeddings:

1. Check your database schema: `\d embeddings` in psql
2. Verify your embedding model's output dimension
3. Ensure they match

### Changing Dimensions

You cannot change vector dimensions on an existing table. You must:

1. Create a migration plan
2. Back up existing data
3. Drop and recreate the table
4. Re-embed all documents

See the migration guide in the main README for detailed steps.
