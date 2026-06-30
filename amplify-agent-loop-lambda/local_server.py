"""
local_server.py - Local HTTP server for amplify-agent-loop-lambda

Runs the agent loop as a simple HTTP server on port 3015.
The JS localServer.js will call: POST http://localhost:3015/vu-agent/handle-event

Additional local-test endpoints (scheduled tasks):

  POST /scheduled-tasks/run
      Triggers execute_scheduled_tasks() directly — finds due tasks in
      DynamoDB and sends them to SQS (or logs what it would send).

  POST /scheduled-tasks/process-task
      Accepts a raw task payload and processes it through TasksMessageHandler
      without touching SQS at all.  Body shape:
        {
          "source": "scheduled-task",
          "taskData": { ...full task object... }
        }

Usage:
    cd /path/to/amplify-agent-loop-lambda
    source local_run_vars.sh
    source venv/bin/activate
    python3 local_server.py
"""

import json
import os
import sys
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Load env vars from local_run_vars.sh before importing anything AWS-related
# (should already be set by sourcing local_run_vars.sh)

from pycommon.logger import getLogger
logger = getLogger("local_server")

# Import the core service (this registers all routes including /vu-agent/*)
import service.core
from service.core import route as core_route
from service.handlers import handle_event

# Scheduled-tasks imports (used by the local test endpoints)
from scheduled_tasks_events.scheduled_tasks import execute_scheduled_tasks
from service.agent_queue import route_queue_event


PORT = int(os.environ.get("LOCAL_SERVER_PORT", "3015"))


class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.info(f"HTTP {format}", *args)

    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError as e:
            self._respond(400, {"success": False, "error": f"Invalid JSON: {e}"})
            return

        # Extract auth token
        auth_header = self.headers.get("Authorization", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        logger.info(f"POST {path}")
        logger.debug(f"Body keys: {list(body.keys())}")

        # Strip the /dev prefix that doRequestOp adds when routing locally
        canonical_path = path[4:] if path.startswith("/dev/") else path

        try:
            if canonical_path == "/vu-agent/handle-event":
                result = self._handle_agent_event(body, access_token)
                self._respond(200, result)
            elif canonical_path.startswith("/vu-agent/"):
                # All other /vu-agent/* ops (execute-task, create-scheduled-task, etc.)
                # go through service.core.route just like the real Lambda does
                result = self._handle_vu_agent(canonical_path, body, access_token)
                self._respond(200, result)
            elif canonical_path == "/scheduled-tasks/run":
                result = self._handle_run_scheduled_tasks()
                self._respond(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            elif canonical_path == "/scheduled-tasks/process-task":
                result = self._handle_process_task(body)
                self._respond(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            else:
                self._respond(404, {"success": False, "error": f"Unknown path: {path}"})
        except Exception as e:
            logger.error(f"Error handling {path}: {e}")
            logger.error(traceback.format_exc())
            self._respond(500, {"success": False, "error": str(e)})

    def _handle_vu_agent(self, canonical_path, body, access_token):
        """
        Routes any /vu-agent/* request through service.core.route(),
        exactly as the real Lambda does — just without API Gateway wrapping.
        """
        logger.info(f"Routing via core.route: {canonical_path}")

        # Build a minimal Lambda-style event so core.route can read event["path"]
        fake_event = {
            "path": canonical_path,
            "rawPath": canonical_path,
            "httpMethod": "POST",
            "headers": {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            "body": json.dumps(body),
        }
        result = core_route(fake_event, None)
        # core.route returns {"success": ..., "data": ...} directly (not statusCode/body)
        return result

    def _handle_agent_event(self, body, access_token):
        """Route to handle_event, mirroring how the Lambda handler works."""
        data = body.get("data", body)  # support both {data: {...}} and flat body

        current_user = data.get("currentUser") or body.get("currentUser", "local-dev-user")
        session_id = data.get("sessionId", "local-session-1")
        request_id = data.get("requestId", "local-request-1")
        prompt = data.get("prompt", [])
        metadata = data.get("metadata", {})

        # prompt can be a string (simple case) or list of messages
        if isinstance(prompt, str):
            prompt = [{"role": "user", "content": prompt}]

        logger.info(f"Calling handle_event for user={current_user}, session={session_id}")

        result = handle_event(
            current_user=current_user,
            access_token=access_token,
            session_id=session_id,
            prompt=prompt,
            request_id=request_id,
            metadata=metadata,
        )
        return result

    def _handle_run_scheduled_tasks(self):
        """Calls execute_scheduled_tasks directly — finds due tasks and enqueues them."""
        logger.info("Local test: triggering execute_scheduled_tasks()")
        return execute_scheduled_tasks({}, None)

    def _handle_process_task(self, body):
        """
        Wraps the request body into a fake SQS Records envelope and calls
        route_queue_event() so TasksMessageHandler processes it end-to-end.

        Expected body:
          { "source": "scheduled-task", "taskData": { ...task fields... } }
        """
        logger.info("Local test: processing task directly via route_queue_event()")
        fake_sqs_event = {
            "Records": [
                {
                    "messageId": str(uuid.uuid4()),
                    "receiptHandle": "local-test-receipt-handle",
                    "body": json.dumps(body),
                    "attributes": {},
                    "messageAttributes": {},
                    "md5OfBody": "",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "arn:aws:sqs:local:000000000000:AgentQueue",
                    "awsRegion": "us-east-1",
                }
            ]
        }
        return route_queue_event(fake_sqs_event, None)

    def _respond(self, status_code, data):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    print(f"\n{'=' * 60}")
    print(f"  Amplify Agent Loop - Local Server")
    print(f"  Listening on: http://localhost:{PORT}")
    print(f"  Endpoints:")
    print(f"    POST /vu-agent/*                   (all agent ops — execute-task, create-scheduled-task, etc.)")
    print(f"    POST /scheduled-tasks/run          (trigger scheduler)")
    print(f"    POST /scheduled-tasks/process-task (bypass SQS, run task directly)")
    print(f"{'=' * 60}\n")

    server = HTTPServer(("0.0.0.0", PORT), AgentHandler)
    logger.info(f"Local agent server started on port {PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
