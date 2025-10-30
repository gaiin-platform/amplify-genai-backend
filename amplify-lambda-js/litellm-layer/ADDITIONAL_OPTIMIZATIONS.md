# Additional Optimizations for 50MB+ Reduction

## Current Situation
- **Limit:** 262MB (function code + layers)
- **Current:** 281MB
- **Need to remove:** ~20MB minimum (targeting 50MB for headroom)

## Quick Win: Use Ultra-Slim Build

```bash
cd amplify-lambda-js/litellm-layer
./build-layer-ultra-slim.sh
```

**Expected savings: 50-70MB**

This script does everything from the optimized build PLUS more aggressive stripping.

## If You Still Need More Space

### Option 1: Remove Unused LiteLLM Provider Dependencies

LiteLLM supports many providers, but you probably only use a few. Here's what each provider adds:

| Provider | Packages | Est. Size | Keep If You Use |
|----------|----------|-----------|-----------------|
| OpenAI | openai | ~15MB | Azure OpenAI, OpenAI API |
| Anthropic | anthropic | ~8MB | Claude API |
| Google | google-* | ~12MB | Gemini, PaLM |
| Cohere | cohere | ~8MB | Cohere API |
| Replicate | replicate | ~5MB | Replicate models |
| Hugging Face | transformers | ~50MB+ | Local models (probably remove) |

**To remove unused providers:**

After building the layer, run:

```bash
cd python

# Remove Anthropic if you don't use Claude
rm -rf anthropic*

# Remove Google if you don't use Gemini/PaLM
rm -rf google* proto*

# Remove Cohere if you don't use it
rm -rf cohere*

# Remove Hugging Face (BIG) if you don't use it
rm -rf transformers* torch* tokenizers*

# Remove other AI providers you don't use
rm -rf replicate* ai21* aleph_alpha*
```

### Option 2: Remove Large Transitive Dependencies

Some packages bring in large dependencies you might not need:

```bash
cd python

# If you don't use advanced HTTP features
rm -rf httpcore-* h11-* h2-*  # Save ~5MB

# If you don't use async features heavily
rm -rf anyio-* sniffio-*  # Save ~3MB

# Remove charset detection if not needed
rm -rf charset_normalizer-*  # Save ~2MB

# Remove certifi certificates if using system certs
rm -rf certifi-*  # Save ~1MB
```

### Option 3: Analyze Your Specific Installation

Run this to see what's actually taking up space:

```bash
cd python
du -h * | sort -rh | head -30
```

Look for:
- Large packages you don't recognize
- Multiple versions of the same package
- Packages that seem unrelated to your use case

### Option 4: Use Minimal Requirements

Create a `requirements-minimal.txt`:

```txt
# Minimal LiteLLM installation
litellm==1.78.7 --no-deps

# Add only the providers you actually use
openai>=1.0.0  # If you use OpenAI/Azure
boto3>=1.26.0  # For AWS Bedrock

# Essential dependencies
httpx>=0.24.0
pydantic>=2.0.0
tiktoken>=0.4.0
python-dotenv>=1.0.0
```

Then build with:

```bash
pip install -r requirements-minimal.txt -t python/
```

### Option 5: Split Into Multiple Layers

If you're using multiple Lambda functions, split the dependencies:

**Layer 1: Core Python Runtime** (~50MB)
- Python binary
- Standard library essentials

**Layer 2: LiteLLM + Direct Dependencies** (~80MB)
- litellm
- openai
- httpx
- pydantic

**Layer 3: Optional Providers** (~40MB)
- anthropic
- google-*
- cohere

Then only attach the layers each function needs.

## Expected Savings Summary

| Optimization | Savings | Risk Level |
|--------------|---------|------------|
| Ultra-slim script | 50-70MB | 🟢 Low |
| Remove unused providers | 10-50MB | 🟡 Medium (test thoroughly) |
| Remove large transitive deps | 5-15MB | 🟡 Medium (test thoroughly) |
| Use minimal requirements | 30-50MB | 🔴 High (requires testing) |
| Split into multiple layers | Variable | 🟢 Low (just more complex) |

## Recommended Approach

### Step 1: Try Ultra-Slim Build First
```bash
./build-layer-ultra-slim.sh
```

This should get you **50-70MB savings** with zero risk.

### Step 2: If Still Over, Remove Unused Providers
```bash
cd python
# Only keep providers you actually use
rm -rf anthropic* google* cohere* replicate*  # ~30MB
```

### Step 3: Test Deployment
```bash
cd ..
serverless deploy --stage dev
```

### Step 4: Verify Functionality
- Check CloudWatch logs
- Make test chat requests
- Verify no import errors

## Current Standard Build Breakdown

Typical space usage in litellm layer:

```
litellm/          25MB
openai/           15MB
httpx/            12MB
pydantic/         15MB
boto3/            15MB
anthropic/         8MB
google-*/         12MB
Other deps/       30MB
Python binary/     5MB
Tests/docs/       20MB ← Removed by ultra-slim
Debug symbols/    15MB ← Removed by ultra-slim
Build tools/      12MB ← Removed by ultra-slim
----------------------------
Total:           ~184MB (before optimization)
After ultra-slim: ~117MB (remove 67MB)
```

## Which Providers Do You Use?

To figure out what you can safely remove, check your code:

```bash
# Search for provider usage
cd amplify-lambda-js
grep -r "anthropic" . --include="*.js" --include="*.py"
grep -r "google" . --include="*.js" --include="*.py"
grep -r "cohere" . --include="*.js" --include="*.py"
```

If you only see `openai` and `azure`, you can safely remove all other providers.

## Emergency Option: Ultra-Minimal Build

If you're really desperate, you can build with ONLY what you need:

```bash
# Install litellm without dependencies
pip install litellm==1.78.7 --no-deps -t python/

# Then manually add ONLY what's needed
pip install openai httpx pydantic tiktoken python-dotenv boto3 -t python/

# This might get you down to ~80MB total
```

⚠️ **Warning:** This requires extensive testing as you might miss dependencies.

## Summary

**Quick 50MB win:**
```bash
./build-layer-ultra-slim.sh
```

**If you need more:**
- Remove unused provider packages (~30MB)
- You probably only use OpenAI/Azure, so remove anthropic, google, cohere, etc.

**Bottom line:** The ultra-slim script should get you under 262MB. If not, removing 2-3 unused provider packages will definitely do it.
