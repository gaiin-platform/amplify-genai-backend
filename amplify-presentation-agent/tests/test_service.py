import io
import json
import os

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("PRESENTATION_JOBS_TABLE", "jobs")
os.environ.setdefault("AMPLIFY_ADMIN_DYNAMODB_TABLE", "admin")
os.environ.setdefault("S3_CONSOLIDATION_BUCKET_NAME", "bucket")

from service import core  # noqa: E402


class FakeTable:
    def __init__(self, items=None, key="jobId"):
        self.items = dict(items or {})
        self.key = key
        self.updates = []

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item[self.key]] = Item

    def update_item(self, Key, **kwargs):
        self.updates.append((Key, kwargs))


@pytest.fixture
def tables(monkeypatch):
    admin = FakeTable(
        {
            "powerPointTemplates": {
                "config_id": "powerPointTemplates",
                "data": [
                    {"name": "open.pptx", "isAvailable": True, "amplifyGroups": []},
                    {"name": "group.pptx", "isAvailable": False, "amplifyGroups": ["faculty"]},
                ],
            },
            "presentationAgent": {"config_id": "presentationAgent", "data": {"modelId": "us.anthropic.claude-sonnet-5", "maxReviewPasses": 1}},
        },
        key="config_id",
    )
    jobs = FakeTable()

    class FakeResource:
        def Table(self, name):
            return admin if name == "admin" else jobs

    monkeypatch.setattr(core, "dynamodb", FakeResource())
    return admin, jobs


def test_start_creates_job_and_invokes_runtime(tables, monkeypatch):
    _, jobs = tables
    sent = {}
    monkeypatch.setattr(core, "_invoke_runtime", lambda job_id, payload: sent.update(payload))

    result = core.create_presentation_job("alice", {"templateName": "open.pptx", "content": "# Notes", "title": "Plan"}, "token")

    assert result["success"] is True
    job_id = result["data"]["jobId"]
    assert jobs.items[job_id]["user"] == "alice"
    assert jobs.items[job_id]["status"] == "queued"
    assert sent["action"] == "generate"
    assert sent["modelId"] == "us.anthropic.claude-sonnet-5"
    assert sent["maxReviewPasses"] == 1
    assert sent["accountId"] == "general_account"


def test_start_rejects_template_outside_users_groups(tables, monkeypatch):
    monkeypatch.setattr(core, "verify_user_in_amp_group", lambda token, groups: False)
    monkeypatch.setattr(core, "_invoke_runtime", lambda *a: pytest.fail("runtime must not be invoked"))
    result = core.create_presentation_job("bob", {"templateName": "group.pptx", "content": "x"}, "token")
    assert result["success"] is False
    assert "access" in result["message"]


def test_start_allows_group_member(tables, monkeypatch):
    monkeypatch.setattr(core, "verify_user_in_amp_group", lambda token, groups: groups == ["faculty"])
    monkeypatch.setattr(core, "_invoke_runtime", lambda *a: None)
    assert core.create_presentation_job("carol", {"templateName": "group.pptx", "content": "x"}, "token")["success"]


def test_start_marks_job_failed_when_runtime_unavailable(tables, monkeypatch):
    _, jobs = tables
    monkeypatch.delenv("PRESENTATION_AGENT_RUNTIME_ARN", raising=False)
    result = core.create_presentation_job("alice", {"templateName": "open.pptx", "content": "x"}, "token")
    assert result["success"] is False
    assert jobs.updates and jobs.updates[0][1]["ExpressionAttributeValues"][":s"] == "failed"


def test_status_hides_other_users_jobs(tables):
    _, jobs = tables
    jobs.put_item({"jobId": "j1", "user": "alice", "status": "running", "progress": 40})
    assert core.presentation_status("mallory", "j1")["success"] is False
    assert core.presentation_status("alice", "j1")["data"]["progress"] == 40


def test_status_presigns_completed_results(tables, monkeypatch):
    _, jobs = tables
    monkeypatch.setattr(core, "_presign", lambda key, filename=None: f"https://signed/{key}")
    jobs.put_item(
        {
            "jobId": "j2",
            "user": "alice",
            "status": "completed",
            "progress": 100,
            "result": {"pptxKey": "presentations/alice/j2/Plan.pptx", "slideKeys": ["presentations/alice/j2/slide-01.jpg"], "slideCount": 1, "reviewScores": [7, 9]},
        }
    )
    data = core.presentation_status("alice", "j2")["data"]
    assert data["result"]["fileName"] == "Plan.pptx"
    assert data["result"]["downloadUrl"].endswith("Plan.pptx")
    assert data["result"]["slides"] == ["https://signed/presentations/alice/j2/slide-01.jpg"]
    assert data["result"]["reviewScores"] == [7, 9]


def test_invoke_runtime_raises_on_rejection(monkeypatch):
    monkeypatch.setenv("PRESENTATION_AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123:runtime/x")

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            assert len(kwargs["runtimeSessionId"]) >= 33
            return {"response": io.BytesIO(json.dumps({"success": False, "error": "nope"}).encode())}

    monkeypatch.setattr(core.boto3, "client", lambda *a, **k: FakeClient())
    with pytest.raises(RuntimeError, match="nope"):
        core._invoke_runtime("job", {})
