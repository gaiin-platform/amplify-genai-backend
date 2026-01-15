# How Embedding Models Are Configured

## Overview

The solution uses a multi-tier configuration system to determine which embedding models to use. The configuration is stored in DynamoDB and can be managed through the admin interface.

## Configuration Flow

```
1. Model Catalog (CSV)
   ↓
2. MODEL_RATE_TABLE (DynamoDB)
   ↓
3. AMPLIFY_ADMIN_DYNAMODB_TABLE (DynamoDB)
   ↓
4. Embedding Service (Runtime)
```

## 1. Model Catalog (CSV)

**Location:** `chat-billing/model_rates/model_rate_values.csv`

This CSV file contains all available models and their metadata:

```csv
ModelID,Provider,InputCostPerThousandTokens,OutputCostPerThousandTokens,OutputTokenLimit,...
amazon.titan-embed-text-v1,Bedrock,0.001,0,0,...
text-embedding-3-large,Azure,0.00013,0,0,...
text-embedding-3-small,Azure,0.00002,0,0,...
text-embedding-ada-002,Azure,0.0001,0,0,...
gpt-4o,Azure,0.005,0.015,4096,...
```

### How Embedding Models Are Identified

**Embedding models are distinguished by having BOTH:**
- `OutputCostPerThousandTokens = 0`
- `OutputTokenLimit = 0`

This makes sense because embedding models:
- Don't generate output tokens (they return vectors, not text)
- Don't have output token limits
- Only have input costs

**Available Embedding Models:**
- `amazon.titan-embed-text-v1` (Bedrock) - 1024 or 1536 dimensions
- `text-embedding-3-large` (Azure/OpenAI) - 3072 dimensions (can reduce to 1024)
- `text-embedding-3-small` (Azure/OpenAI) - 1536 dimensions (can reduce to 1024)
- `text-embedding-ada-002` (Azure/OpenAI) - 1536 dimensions

## 2. MODEL_RATE_TABLE (DynamoDB)

**Table Name:** `amplify-{DEP_NAME}-chat-billing-{stage}-model-rates`

**Purpose:** Stores all available models with their pricing and capabilities

**How it's populated:**
- The `chat-billing/model_rates/update_table.py` script loads data from the CSV
- Deployed via the `chat-billing` service
- Lambda function: `load_model_rates`

**Key Fields:**
- `ModelID` (Primary Key) - e.g., "text-embedding-3-small"
- `Provider` - "Azure", "OpenAI", or "Bedrock"
- `InputCostPerThousandTokens` - Cost per 1K tokens
- `ModelName` - Display name

## 3. AMPLIFY_ADMIN_DYNAMODB_TABLE (DynamoDB)

**Table Name:** `amplify-{DEP_NAME}-admin-{stage}-admin-configs`

**Purpose:** Stores system-wide configuration including default models

**Key Item:**
```json
{
  "config_id": "defaultModels",
  "data": {
    "user": "gpt-4o-mini",
    "advanced": "gpt-4o",
    "cheapest": "mistral.mistral-7b-instruct-v0:2",
    "embeddings": "text-embedding-3-small",
    "qa": "gpt-4o-mini",
    "agent": "gpt-4o",
    "documentCaching": "gpt-4o-mini"
  },
  "last_updated": "2024-12-16T10:30:00Z"
}
```

**How it's set:**

### Option A: Automatic Detection (Legacy)
The `chat-billing/service/core.py` function `extract_and_update_default_models()` scans the MODEL_RATE_TABLE for models marked with special flags:
- `DefaultEmbeddingsModel: true`
- `UsersDefault: true`
- `DefaultAdvancedModel: true`
- etc.

### Option B: Admin Interface (Recommended)
Admins can update default models through the admin API:

**Endpoint:** `POST /amplifymin/configs/update`

**Payload:**
```json
{
  "type": "defaultModels",
  "data": {
    "embeddings": "text-embedding-3-small",
    "qa": "gpt-4o-mini",
    "user": "gpt-4o-mini",
    "advanced": "gpt-4o",
    "cheapest": "mistral.mistral-7b-instruct-v0:2"
  }
}
```

**Schema:** Defined in `amplify-lambda-admin/schemata/update_admin_config_schema.py`

## 4. Embedding Service (Runtime)

**Location:** `embedding/embedding_models.py` and `embedding/shared_functions.py`

### How Models Are Retrieved

```python
# embedding/embedding_models.py
def get_embedding_models():
    # 1. Get the admin table
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    
    # 2. Get default model IDs from admin config
    response = admin_table.get_item(Key={"config_id": "defaultModels"})
    default_models = response["Item"]["data"]
    embedding_model_id = default_models["embeddings"]  # e.g., "text-embedding-3-small"
    qa_model_id = default_models["cheapest"]
    
    # 3. Get provider info from model rate table
    response = model_rate_table.get_item(Key={"ModelID": embedding_model_id})
    provider = response["Item"]["Provider"]  # e.g., "Azure"
    
    # 4. Return model configuration
    return {
        "success": True,
        "data": {
            "embedding": {
                "model_id": embedding_model_id,
                "provider": provider
            },
            "qa": {...}
        }
    }
```

### How Models Are Used

```python
# embedding/shared_functions.py
# Called at module load time
model_result = get_embedding_models()
if model_result["success"]:
    data = model_result["data"]
    embedding_model_name = data["embedding"]["model_id"]
    embedding_provider = data["embedding"]["provider"]
    qa_model_name = data["qa"]["model_id"]
    qa_provider = data["qa"]["provider"]

# Later, when generating embeddings
def generate_embeddings(content):
    if embedding_provider == "Bedrock":
        return generate_bedrock_embeddings(content)
    elif embedding_provider == "Azure":
        return generate_azure_embeddings(content)
    elif embedding_provider == "OpenAI":
        return generate_openai_embeddings(content)
```

## Changing the Embedding Model

### Method 1: Via Admin API (Recommended)

1. **Ensure the model exists in MODEL_RATE_TABLE**
   - Check `chat-billing/model_rates/model_rate_values.csv`
   - If not present, add it and redeploy `chat-billing` service

2. **Update the default models configuration**
   ```bash
   curl -X POST https://your-api.com/amplifymin/configs/update \
     -H "Authorization: Bearer $TOKEN" \
     -d '{
       "type": "defaultModels",
       "data": {
         "embeddings": "amazon.titan-embed-text-v1"
       }
     }'
   ```

3. **Restart embedding service**
   - The model configuration is loaded at Lambda cold start
   - Either wait for natural cold start or redeploy

### Method 2: Via DynamoDB Console

1. Open DynamoDB console
2. Navigate to `amplify-{DEP_NAME}-admin-{stage}-admin-configs` table
3. Find item with `config_id = "defaultModels"`
4. Edit the `data.embeddings` field
5. Restart embedding service

### Method 3: Via CSV (For New Models)

1. **Add model to CSV**
   ```csv
   # chat-billing/model_rates/model_rate_values.csv
   # Format: ModelID,AdditionalSystemPrompt,Available,Built-In,Description,ExclusiveGroupAvailability,
   #         InputContextWindow,InputCostPerThousandTokens,CachedCostPerThousandTokens,ModelName,
   #         OutputCostPerThousandTokens,OutputTokenLimit,Provider,SupportsImages,SupportsSystemPrompts,SupportsReasoning
   
   cohere.embed-english-v3,,FALSE,TRUE,,[],0,0.0001,0,Cohere-Embed-v3,0,0,Bedrock,FALSE,FALSE,FALSE
   ```
   
   **CRITICAL for embedding models:**
   - `OutputCostPerThousandTokens` MUST be `0`
   - `OutputTokenLimit` MUST be `0`
   - This is how the admin UI identifies embedding models for the dropdown

2. **Deploy chat-billing service**
   ```bash
   cd chat-billing
   serverless deploy --stage dev
   ```

3. **Invoke load_model_rates Lambda**
   - This populates MODEL_RATE_TABLE from CSV

4. **Set as default** (via Method 1 or 2)

## Important Notes

### Model Dimensions Must Match Database

The embedding model you choose MUST produce vectors matching your database dimension:

- **Database dimension:** Set via `EMBEDDING_DIM` in var file (see `EMBEDDING_DIMENSIONS.md`)
- **Model output:** Must match the database dimension

**Examples:**
- Database: 1024 dims → Use: `amazon.titan-embed-text-v2:0` (with dimensions=1024)
- Database: 1536 dims → Use: `text-embedding-3-small` (default)
- Database: 1024 dims → Use: `text-embedding-3-small` (with dimensions=1024 parameter)

### Provider-Specific Configuration

**Bedrock Models:**
- Require AWS credentials and region configuration
- Model IDs follow pattern: `amazon.titan-*`, `cohere.*`
- Invoked via `boto3.client('bedrock-runtime')`

**Azure/OpenAI Models:**
- Require endpoint and API key from `LLM_ENDPOINTS_SECRETS_NAME_ARN`
- Model IDs: `text-embedding-*`, `gpt-*`
- Invoked via OpenAI SDK

### QA Model

The `qa` model is used for generating questions from document chunks (dual retrieval):
- Should be a chat/completion model, not an embedding model
- Used in `shared_functions.py::generate_questions()`
- Typically set to a cheap, fast model like `gpt-4o-mini`

## Troubleshooting

### "No Default Models Data Found"
- Check that `defaultModels` item exists in AMPLIFY_ADMIN_DYNAMODB_TABLE
- Run `extract_and_update_default_models()` to auto-populate from MODEL_RATE_TABLE

### "Invalid embedding provider"
- Verify the provider in MODEL_RATE_TABLE is one of: "Azure", "OpenAI", "Bedrock"
- Check for typos in provider name

### Dimension Mismatch Errors
- Verify database dimension: `SELECT * FROM pg_type WHERE typname = 'vector';`
- Check model output dimension matches database
- See `EMBEDDING_DIMENSIONS.md` for migration guide

### Model Not Found
- Ensure model exists in MODEL_RATE_TABLE
- Check ModelID spelling matches exactly
- Verify model is available in your region (for Bedrock)

## How the Admin UI Identifies Embedding Models

The admin interface needs to show only embedding models in the "Default Embeddings Model" dropdown. It identifies embedding models using a simple pattern:

**A model is considered an embedding model if:**
```
OutputCostPerThousandTokens == 0 AND OutputTokenLimit == 0
```

**Why this works:**
- Embedding models don't generate text output, they return vectors
- They have no output tokens, only input tokens
- They have no output token limits
- They only incur input costs

**Example comparison:**

| Model | Type | OutputCost | OutputLimit | Is Embedding? |
|-------|------|------------|-------------|---------------|
| text-embedding-3-small | Embedding | 0 | 0 | ✅ Yes |
| amazon.titan-embed-text-v1 | Embedding | 0 | 0 | ✅ Yes |
| gpt-4o | Chat | 0.015 | 4096 | ❌ No |
| gpt-4o-mini | Chat | 0.00066 | 16384 | ❌ No |

**When adding a new embedding model to the CSV:**
1. Set `OutputCostPerThousandTokens` to `0`
2. Set `OutputTokenLimit` to `0`
3. Set `InputCostPerThousandTokens` to the actual cost per 1K input tokens
4. The admin UI will automatically include it in the embeddings dropdown

## Summary

The embedding model configuration is a three-tier system:
1. **CSV** defines available models (embedding models have OutputCost=0 and OutputLimit=0)
2. **MODEL_RATE_TABLE** stores model metadata
3. **AMPLIFY_ADMIN_DYNAMODB_TABLE** stores which models are defaults
4. **Embedding service** reads defaults at runtime

To change models, update the admin config via API or DynamoDB console, ensuring the model's output dimensions match your database schema.
