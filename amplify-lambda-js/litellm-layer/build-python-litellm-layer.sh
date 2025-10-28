#!/usr/bin/env bash
set -euo pipefail

# ========= Config =========
ARCH="${ARCH:-arm64}"        # arm64 or x86_64
PYVER="3.11"
REL="20241002"               # python-build-standalone tag
WORK="layer-build-${ARCH}"
ROOT="${WORK}/layer"
PYROOT="${ROOT}/python"
SITE="${PYROOT}/lib/python${PYVER}"
STAGE="${WORK}/staging"
PKGZIP="${SITE}/site-packages.zip"

# Select tarball name for arch
case "${ARCH}" in
  arm64)   TAR="cpython-${PYVER}.${REL}-aarch64-unknown-linux-gnu-install_only.tar.zst" ;;
  x86_64)  TAR="cpython-${PYVER}.${REL}-x86_64-unknown-linux-gnu-install_only.tar.zst" ;;
  *) echo "Unknown ARCH=${ARCH}. Use arm64 or x86_64." >&2; exit 2 ;;
esac

# ========= Clean =========
rm -rf "${WORK}"
mkdir -p "${PYROOT}" "${STAGE}" "${SITE}"

# ========= Fetch relocatable CPython =========
echo "[*] Downloading python-build-standalone ${PYVER} ${ARCH}"
curl -L -o "${WORK}/py.tar.zst" \
  "https://github.com/indygreg/python-build-standalone/releases/download/${REL}/${TAR}"

# Optional integrity check. Set PY_TAR_SHA256 env to enforce a known hash.
if [[ -n "${PY_TAR_SHA256:-}" ]]; then
  echo "${PY_TAR_SHA256}  ${WORK}/py.tar.zst" | sha256sum -c -
fi

echo "[*] Extracting CPython"
tar --zstd -C "${WORK}" -xf "${WORK}/py.tar.zst"
cp -a "${WORK}/python/." "${PYROOT}/"

# Verify interpreter
"${PYROOT}/bin/python${PYVER}" -V

# ========= Minimal pip and constraints =========
"${PYROOT}/bin/python${PYVER}" -m ensurepip --upgrade
PIP="${PYROOT}/bin/pip${PYVER}"

# Constrain to only what litellm needs for OpenAI. Adjust if you need Azure OpenAI too.
cat > "${WORK}/constraints.txt" <<'EOF'
litellm==1.45.0
httpx==0.27.2
pydantic==2.9.2
pydantic-core==2.23.4
typing-extensions==4.12.2
anyio==4.4.0
sniffio==1.3.1
idna==3.7
certifi==2024.8.30
h11==0.14.0
EOF

# ========= Install wheels only to staging =========
"${PIP}" install --upgrade --no-compile --no-deps --only-binary=:all: \
  --constraint "${WORK}/constraints.txt" -t "${STAGE}" \
  certifi idna sniffio h11 anyio typing-extensions pydantic-core pydantic httpx litellm

# ========= Hard prune litellm providers (OpenAI only by default) =========
rm -rf "${STAGE}/litellm/proxy" || true
for p in anthropic bedrock vertex azure_ai_inference google_ai_studio mistral groq openrouter \
         ollama replicate together deepseek xai cohere solar meta others azure_openai;
do rm -rf "${STAGE}/litellm/llms/$p" || true; done
# keep litellm/llms/openai

# ========= Hygiene prune =========
find "${STAGE}" -type d -name "__pycache__" -prune -exec rm -rf {} + || true
find "${STAGE}" -type f -name "*.pyc" -delete || true
find "${STAGE}" -type d -name "tests" -prune -exec rm -rf {} + || true
find "${STAGE}" -type d -name "docs" -prune -exec rm -rf {} + || true
find "${STAGE}" -type f -name "*.pyi" -delete || true

# Keep minimal dist-info for importlib.metadata
find "${STAGE}" -type d -name "*.dist-info" | while read d; do
  find "$d" -type f ! -name "METADATA" ! -name "WHEEL" ! -name "entry_points.txt" -delete || true
done

# ========= Pack into a single importable zip =========
echo "[*] Creating site-packages.zip"
( cd "${STAGE}" && zip -9 -r "${PKGZIP}" . >/dev/null )

# ========= sitecustomize to load the zip and set CA bundle =========
cat > "${SITE}/sitecustomize.py" <<'PY'
import os, sys
z = os.path.join(os.path.dirname(__file__), "site-packages.zip")
if os.path.exists(z) and z not in sys.path:
    sys.path.insert(0, z)
try:
    import certifi, os
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass
PY

# ========= Trim stdlib you do not need =========
rm -rf "${PYROOT}/lib/python${PYVER}/test" || true
rm -rf "${PYROOT}/lib/python${PYVER}/distutils" || true
rm -rf "${PYROOT}/lib/python${PYVER}/idlelib" || true
rm -rf "${PYROOT}/lib/python${PYVER}/tkinter" || true
rm -rf "${PYROOT}/lib/python${PYVER}/ensurepip" || true

# Some builds ship static objects or dev files that are safe to prune
find "${PYROOT}" -type f \( -name "*.a" -o -name "*.pyc" \) -delete || true

# Symlink python3 for convenience
ln -sf "python${PYVER}" "${PYROOT}/bin/python3"

# ========= Final artifact =========
echo "[*] Zipping layer"
( cd "${ROOT}" && zip -9 -r "../python-litellm-${ARCH}.zip" . >/dev/null )

echo "[*] Done. Artifact:"
du -h "${WORK}/python-litellm-${ARCH}.zip"
