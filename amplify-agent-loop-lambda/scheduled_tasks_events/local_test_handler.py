"""
LOCAL TESTING ONLY — do NOT deploy to production.

Exposes two HTTP endpoints that let you exercise the scheduled-tasks
pipeline without a real CloudWatch schedule or a real SQS queue:

  POST /dev/scheduled-tasks/run
      Calls execute_scheduled_tasks() directly.
      This runs find_tasks_to_execute() → send_tasks_to_queue(), so it
      needs real DynamoDB / SQS access (or localstack).

  POST /dev/scheduled-tasks/process-task
      Accepts a raw task object in the request body and wraps it into a
      fake SQS Records envelope, then calls route_queue_event() directly.
      This lets you test TasksMessageHandler end-to-end without sending
      a real SQS message.

      Expected request body (JSON):
      {
          "source": "scheduled-task",
          "taskData": { ...full task object... }
      }
      The handler wraps this into the SQS Records shape that
      route_queue_event() expects, so you can paste in any task payload.
"""

import json
import uuid


# ── Step 1 endpoint: trigger the scheduler (find tasks → send to queue) ──────

def run_scheduled_tasks(event, context):
    """
    HTTP shim for execute_scheduled_tasks.
    Maps: POST /dev/scheduled-tasks/run
    """
    from scheduled_tasks_events.scheduled_tasks import execute_scheduled_tasks
    return execute_scheduled_tasks(event, context)


# ── Step 2 endpoint: process a single task message bypassing SQS ─────────────

def process_task_directly(event, context):
    """
    HTTP shim that wraps a JSON body into a fake SQS Records envelope
    and feeds it straight into route_queue_event().
    Maps: POST /dev/scheduled-tasks/process-task

    Body shape:
    {
        "source": "scheduled-task",
        "taskData": { ...task fields... }
    }
    """
    from service.agent_queue import route_queue_event

    try:
        body = event.get("body") or "{}"
        if isinstance(body, str):
            message_body = json.loads(body)
        else:
            message_body = body
    except json.JSONDecodeError as exc:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Invalid JSON body: {exc}"}),
        }

    # Wrap into the SQS Records envelope that route_queue_event() iterates over.
    fake_sqs_event = {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "local-test-receipt-handle",
                "body": json.dumps(message_body),
                "attributes": {},
                "messageAttributes": {},
                "md5OfBody": "",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:local:000000000000:AgentQueue",
                "awsRegion": "us-east-1",
            }
        ]
    }

    result = route_queue_event(fake_sqs_event, context)
    return result
