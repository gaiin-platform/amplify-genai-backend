#!/usr/bin/env python3
"""Local-only HTTP adapter for the production Projects business functions.

The adapter deliberately bypasses API Gateway authentication and IAM decorators,
binds to loopback by default, and refuses to start unless PROJECTS_LOCAL_MODE is
explicitly enabled. The underlying CRUD and ownership code is the same code used
by the deployed Lambda handlers.
"""

import inspect
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Running this file directly makes `local/` the first import root. Add the
# service directory's parent so imports match the Lambda runtime layout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urllib.parse import urlparse

if os.getenv("PROJECTS_LOCAL_MODE", "").lower() != "true":
    raise RuntimeError("Set PROJECTS_LOCAL_MODE=true to start the local Projects server")

# Refuse to run against anything but DynamoDB Local on loopback, so this
# auth-bypassing adapter can never touch real AWS resources with ambient
# credentials. (service.projects enforces the same rule on import.)
_endpoint = os.getenv("DYNAMODB_ENDPOINT_URL", "")
if (urlparse(_endpoint).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
    raise RuntimeError("DYNAMODB_ENDPOINT_URL must point at a loopback DynamoDB Local instance")

from pycommon.lzw import lzw_uncompress  # noqa: E402
from service import project_files, project_memory, projects  # noqa: E402
from schemata.schema_validation_rules import rules  # noqa: E402

try:
    import jsonschema
except ImportError as import_error:  # pragma: no cover
    raise RuntimeError("jsonschema is required so local requests are validated like production") from import_error

LOCAL_USER = os.getenv("PROJECTS_LOCAL_USER", "local-projects-user")
HOST = os.getenv("PROJECTS_LOCAL_HOST", "127.0.0.1")
PORT = int(os.getenv("PROJECTS_LOCAL_PORT", "3020"))


def decode_request_data(raw_body):
    """Decode a request body, undoing client-side LZW compression when present.

    Uses the real pycommon.lzw decompressor rather than a local
    reimplementation, so this matches production exactly — including the
    Unicode-tag (U+XXXX) postprocessing step that non-ASCII project content
    (emoji, accented names, etc.) round-trips through. pycommon.lzw_uncompress
    already parses the JSON itself, so its result is used directly.
    """
    if not raw_body:
        return {}
    envelope = json.loads(raw_body)
    payload = envelope.get("data", {})
    if isinstance(payload, list) and all(isinstance(item, int) for item in payload):
        payload = lzw_uncompress(payload)
    return payload if isinstance(payload, dict) else {}


def raw_handler(decorated):
    candidate = inspect.unwrap(decorated)
    visited = set()
    while callable(candidate) and id(candidate) not in visited:
        visited.add(id(candidate))
        if len(inspect.signature(candidate).parameters) >= 5:
            return candidate
        nested = [
            cell.cell_contents
            for cell in (getattr(candidate, "__closure__", None) or [])
            if callable(cell.cell_contents)
        ]
        if not nested:
            break
        candidate = max(
            nested,
            key=lambda function: len(inspect.signature(function).parameters),
        )
    raise RuntimeError(f"Could not locate business handler for {decorated}")


ROUTES = {
    ("POST", "/project/create"): (raw_handler(projects.create_project), "create"),
    ("GET", "/project/list"): (raw_handler(projects.list_projects), "list"),
    ("POST", "/project/get"): (raw_handler(projects.get_project), "get"),
    ("POST", "/project/update"): (raw_handler(projects.update_project), "update"),
    ("POST", "/project/delete"): (raw_handler(projects.delete_project), "delete"),
    ("POST", "/project/files/add"): (raw_handler(project_files.add_project_file), "add"),
    ("POST", "/project/files/list"): (raw_handler(project_files.list_project_files), "list"),
    ("POST", "/project/files/update"): (raw_handler(project_files.update_project_file), "update"),
    ("POST", "/project/files/remove"): (raw_handler(project_files.remove_project_file), "remove"),
    ("POST", "/project/memory/add"): (
        raw_handler(project_memory.add_project_memory),
        "add",
    ),
    ("POST", "/project/memory/list"): (
        raw_handler(project_memory.list_project_memories),
        "list",
    ),
    ("POST", "/project/memory/edit"): (
        raw_handler(project_memory.edit_project_memory),
        "edit",
    ),
    ("POST", "/project/memory/delete"): (
        raw_handler(project_memory.delete_project_memory),
        "delete",
    ),
}


class ProjectsRequestHandler(BaseHTTPRequestHandler):
    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "http://localhost:3000")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def _send(self, value, status=200):
        self._headers(status)
        self.wfile.write(json.dumps(value).encode("utf-8"))

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        path = urlparse(self.path).path
        if path == "/health":
            self._send({"success": True, "mode": "local", "user": LOCAL_USER})
            return
        if path.startswith("/dev/"):
            path = path[4:]
        route = ROUTES.get((method, path))
        if not route:
            self._send({"success": False, "message": "Local route not found."}, 404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = decode_request_data(raw_body)
            handler, operation = route
            # Production validates every request against these schemas in the
            # @validated decorator, which this adapter bypasses; do it here so
            # local runs reject the same inputs production would.
            schema = rules["validators"].get(path, {}).get(operation, {})
            try:
                jsonschema.validate(instance=payload, schema=schema)
            except jsonschema.ValidationError as validation_error:
                self._send({"success": False, "message": f"Invalid request: {validation_error.message}"}, 400)
                return
            result = handler({}, None, LOCAL_USER, path, {"data": payload})
            self._send(result)
        except Exception as error:
            self._send(
                {"success": False, "message": f"Local Projects error: {error}"},
                500,
            )

    def log_message(self, message_format, *args):
        print(f"[projects-local] {self.address_string()} {message_format % args}")


print(f"Local Projects API: http://{HOST}:{PORT}/dev/project/list")
print(f"Local test user: {LOCAL_USER}")
ThreadingHTTPServer((HOST, PORT), ProjectsRequestHandler).serve_forever()
