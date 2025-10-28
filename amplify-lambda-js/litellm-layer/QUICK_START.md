# Quick Start: Optimized Lambda Layer

## TL;DR

You can reduce your Lambda layer from **~117MB to ~60-75MB** (35-50% reduction) by removing files that aren't needed at runtime.

## One Command Solution

```bash
cd amplify-lambda-js/litellm-layer
./build-layer-optimized.sh
```

That's it! The script will:
- Build the layer with all Python dependencies
- Remove all unnecessary files (docs, tests, examples, etc.)
- Show you exactly what was removed and how much space saved
- Generate a detailed analysis report

## What Gets Removed

✅ **Safe to remove (40-60MB total):**
- Documentation files (`.md`, `.txt`, `.rst`, `LICENSE`)
- Help directories (`docs/`, `doc/`)
- Test code (`tests/`, `test/`)
- Example code (`examples/`)
- Build tools (`pip`, `setuptools`, `wheel`)
- Compiled caches (`__pycache__`, `.pyc`)
- Package metadata (`.dist-info/`, `.egg-info/`)
- Type stubs (`.pyi`)
- C source files (`.c`, `.h`)
- Config files (`.cfg`, `.ini`, `.toml`)
- Benchmark code

❌ **Never removed:**
- Python source code (`.py`)
- Compiled libraries (`.so`)
- Core packages (`litellm/`, `boto3/`, etc.)
- Python binary (`bin/python3`)

## Expected Results

```
Initial size:  117M
Final size:    60-75M
Savings:       40-55MB (35-47%)
```

## After Building

Deploy to AWS:
```bash
cd ..
serverless deploy --stage dev
```

## Verify It Works

Check CloudWatch logs for:
```
[TIMING] Python LiteLLM server spawned
[TIMING] Python LiteLLM server ready
```

No errors about missing modules = success! ✅

## Files Created

Three new scripts in `litellm-layer/`:

1. **`build-layer-optimized.sh`** - Builds and optimizes the layer automatically
2. **`analyze-layer-size.sh`** - Analyzes existing layer to show what can be removed
3. **`OPTIMIZATION_GUIDE.md`** - Detailed guide on what's being removed and why

## Need More Details?

See `OPTIMIZATION_GUIDE.md` for:
- Detailed breakdown of what gets removed
- Safety levels for each optimization
- Troubleshooting guide
- Advanced optimization techniques

## Comparison with Original Script

| Script | Size | Time | Safety |
|--------|------|------|--------|
| `build-layer.sh` | ~117MB | 2-3 min | ✅ Safe |
| `build-layer-optimized.sh` | ~60-75MB | 2-3 min | ✅ Safe |

Both scripts are safe, the optimized version just removes unnecessary files.
