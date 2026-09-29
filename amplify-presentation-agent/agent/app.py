"""Amazon Bedrock AgentCore Runtime entrypoint for the Amplify presentation agent.

Invocations return immediately; work runs as a tracked async task so the
runtime reports HealthyBusy while a deck is being built. Progress and results
are written to the presentation jobs table, which the Amplify API polls.

Payloads (sent by the amplify-presentation-agent Lambdas, which authenticate
the user and authorize the template before invoking):
  {"action": "generate", "jobId", "user", "accountId", "templateName", "content",
   "title"?, "instructions"?, "modelId"?, "visionModelId"?, "imageModelId"?, "maxReviewPasses"?}
  {"action": "induct", "jobId", "templateName", "modelId"?}
"""

import logging
import os
import threading
import traceback

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from presentation_agent.billing import record_charges
from presentation_agent.images import make_image_generator
from presentation_agent.induct import induct_template
from presentation_agent.llm import UsageTracker
from presentation_agent.pipeline import GenerateRequest, generate
from presentation_agent.stores import DynamoJobStore, S3BlobStore

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("presentation_agent.app")

app = BedrockAgentCoreApp()


def _stores():
    blobs = S3BlobStore(os.environ["S3_CONSOLIDATION_BUCKET_NAME"], os.environ.get("S3_CONVERSION_OUTPUT_BUCKET_NAME"))
    jobs = DynamoJobStore(os.environ["PRESENTATION_JOBS_TABLE"])
    return blobs, jobs


def _mark_failed(jobs, job_id: str, message: str, error: Exception) -> None:
    logger.error("%s %s: %s\n%s", message, job_id, error, traceback.format_exc())
    if jobs is None:
        return
    try:
        jobs.update(job_id, status="failed", stage="failed", message=message, error=str(error)[:500])
    except Exception as e:
        logger.error("Could not record failure for %s: %s", job_id, e)


def _run_generate(payload: dict, task_id: int) -> None:
    job_id = payload["jobId"]
    usage = UsageTracker()
    image_model_id = payload.get("imageModelId") or None
    duration = 0.0
    images = 0
    jobs = None
    try:
        blobs, jobs = _stores()
        request = GenerateRequest(
            job_id=job_id,
            user=payload["user"],
            template_name=payload["templateName"],
            content=payload["content"],
            title=payload.get("title"),
            instructions=payload.get("instructions"),
            model_id=payload.get("modelId") or None,
            vision_model_id=payload.get("visionModelId") or None,
            image_model_id=image_model_id,
            max_review_passes=int(payload.get("maxReviewPasses", 2)),
        )
        image_generator = make_image_generator(image_model_id) if image_model_id else None
        result = generate(request, blobs, jobs, image_generator=image_generator, usage=usage)
        duration, images = result.duration_seconds, result.images_generated
        jobs.update(job_id, status="completed", stage="completed", progress=100, message="Presentation ready", result=result.to_dict())
    except Exception as e:
        _mark_failed(jobs, job_id, "Presentation generation failed", e)
    finally:
        try:
            record_charges(payload.get("user"), payload.get("accountId"), job_id, usage.as_dict(), duration, images, image_model_id)
        except Exception as e:
            logger.error("Billing failed for job %s: %s", job_id, e)
        app.complete_async_task(task_id)


def _run_induct(payload: dict, task_id: int) -> None:
    job_id = payload["jobId"]
    jobs = None
    try:
        blobs, jobs = _stores()
        jobs.update(job_id, status="running", stage="analyzing", progress=20, message="Analyzing template layouts")
        outcome = induct_template(payload["templateName"], blobs, payload.get("modelId"))
        layouts = len(outcome["catalog"]["layouts"])
        jobs.update(job_id, status="completed", stage="completed", progress=100, message=f"{layouts} layouts analyzed", result={"layouts": layouts})
    except Exception as e:
        _mark_failed(jobs, job_id, "Template analysis failed", e)
    finally:
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload: dict, context=None):
    action = payload.get("action", "generate")
    job_id = payload.get("jobId")
    if not job_id:
        return {"success": False, "error": "jobId is required"}
    runners = {"generate": _run_generate, "induct": _run_induct}
    runner = runners.get(action)
    if runner is None:
        return {"success": False, "error": f"Unknown action: {action}"}
    task_id = app.add_async_task(f"presentation-{action}", {"jobId": job_id})
    threading.Thread(target=runner, args=(payload, task_id), daemon=True).start()
    return {"success": True, "jobId": job_id, "status": "accepted"}


if __name__ == "__main__":
    app.run()
