#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_dir="$script_dir/.venv-projects-local"

cd "$script_dir"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, then run this command again." >&2
  exit 1
fi

docker compose -f docker-compose.projects-local.yml up -d

# Lambda runs Python 3.11 (serverless.yml); prefer it locally so behavior matches.
python_bin="${PYTHON_BIN:-$(command -v python3.11 || command -v python3)}"
if ! "$python_bin" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)'; then
  echo "Warning: using $($python_bin --version) but Lambda runs Python 3.11 (set PYTHON_BIN to override)." >&2
fi

if [ ! -x "$venv_dir/bin/python" ]; then
  "$python_bin" -m venv "$venv_dir"
fi

"$venv_dir/bin/pip" install -q -r requirements-local.txt

export AWS_ACCESS_KEY_ID=local
export AWS_SECRET_ACCESS_KEY=local
export AWS_REGION=us-east-1
export DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8000
export PROJECTS_DYNAMODB_TABLE=amplify-projects-local
export PROJECT_MEMORIES_DYNAMODB_TABLE=amplify-project-memories-local
export PROJECT_FILES_DYNAMODB_TABLE=amplify-project-files-local
export PROJECTS_LOCAL_MODE=true
export PROJECTS_LOCAL_HOST=127.0.0.1
export PROJECTS_LOCAL_PORT="${PROJECTS_LOCAL_PORT:-3020}"
export PROJECTS_LOCAL_USER="${PROJECTS_LOCAL_USER:-local-projects-user}"

for attempt in $(seq 1 30); do
  if curl -sS "$DYNAMODB_ENDPOINT_URL" >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "DynamoDB Local did not become ready." >&2
    exit 1
  fi
  sleep 1
done

"$venv_dir/bin/python" local/init_projects_tables.py
exec "$venv_dir/bin/python" local/projects_server.py
