"""Record model usage and runtime charges in Amplify's billing tables.

Mirrors how amplify-assistants/codeinterpreter records AgentCore charges:
token usage via `record_usage`, compute via `record_additional_charge(flat_cost=...)`.
"""

import logging
from typing import Dict

logger = logging.getLogger("presentation_agent.billing")

RUNTIME_MODEL_ID = "agentcore-presentation-runtime"
# AgentCore Runtime consumption pricing (us-east-1) and the container's sizing.
VCPU_HOUR_USD = 0.0895
GB_HOUR_USD = 0.00945
ASSUMED_VCPUS = 2
ASSUMED_GB = 4
# Nova Canvas standard-quality 1280x720 image.
IMAGE_COST_USD = 0.06


def record_charges(user: str, account_id: str, job_id: str, usage: Dict[str, Dict[str, int]], duration_seconds: float, images: int, image_model_id: str = None) -> None:
    try:
        from pycommon.api.accounting import record_additional_charge, record_usage
    except ImportError:
        logger.warning("pycommon not installed; skipping billing for job %s", job_id)
        return

    account = {"user": user, "account_id": account_id or "general_account"}
    for model_id, counts in usage.items():
        record_usage(
            account=account,
            request_id=job_id,
            model_id=model_id,
            input_tokens=counts.get("inputTokens", 0),
            output_tokens=counts.get("outputTokens", 0),
            input_cached_tokens=counts.get("cacheReadInputTokens", 0),
            input_write_cached_tokens=counts.get("cacheWriteInputTokens", 0),
            details={"feature": "presentationAgent", "jobId": job_id},
        )

    hours = duration_seconds / 3600
    compute = hours * (ASSUMED_VCPUS * VCPU_HOUR_USD + ASSUMED_GB * GB_HOUR_USD)
    record_additional_charge(
        account=account,
        model_id=RUNTIME_MODEL_ID,
        token_count=0,
        item_type="presentationAgentRuntime",
        request_id=job_id,
        details={"durationSeconds": round(duration_seconds, 1)},
        flat_cost=round(compute, 6),
    )
    if images and image_model_id:
        record_additional_charge(
            account=account,
            model_id=image_model_id,
            token_count=0,
            item_type="presentationAgentImages",
            request_id=job_id,
            details={"images": images},
            flat_cost=round(images * IMAGE_COST_USD, 4),
        )
