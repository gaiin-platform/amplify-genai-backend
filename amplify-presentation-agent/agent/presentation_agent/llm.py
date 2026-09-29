"""Strands + Bedrock model access with per-model token accounting."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Type, TypeVar, Union

from botocore.config import Config
from pydantic import BaseModel
from strands import Agent
from strands.models import BedrockModel

from .stores import aws_region

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL_ID = "us.anthropic.claude-opus-5"
MAX_OUTPUT_TOKENS = 32000

ContentBlock = Dict


def text_block(text: str) -> ContentBlock:
    return {"text": text}


def image_block(png: bytes, fmt: str = "png") -> ContentBlock:
    return {"image": {"format": fmt, "source": {"bytes": png}}}


@dataclass
class UsageTracker:
    by_model: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, model_id: str, usage: Dict) -> None:
        bucket = self.by_model[model_id]
        bucket["inputTokens"] += int(usage.get("inputTokens", 0))
        bucket["outputTokens"] += int(usage.get("outputTokens", 0))
        bucket["cacheReadInputTokens"] += int(usage.get("cacheReadInputTokens", 0))
        bucket["cacheWriteInputTokens"] += int(usage.get("cacheWriteInputTokens", 0))

    def as_dict(self) -> Dict[str, Dict[str, int]]:
        return {model: dict(counts) for model, counts in self.by_model.items()}


class LLM:
    def __init__(self, model_id: str, usage: UsageTracker, region: str = None):
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.usage = usage
        self.region = region or aws_region()

    def _model(self) -> BedrockModel:
        return BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=MAX_OUTPUT_TOKENS,
            boto_client_config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=600),
        )

    def structured(self, system: str, content: Union[str, List[ContentBlock]], output_model: Type[T]) -> T:
        """One-shot structured call. A fresh Agent per call keeps stages independent."""
        agent = Agent(model=self._model(), system_prompt=system, callback_handler=None)
        prompt = [text_block(content)] if isinstance(content, str) else content
        result = agent(prompt, structured_output_model=output_model)
        self.usage.add(self.model_id, result.metrics.accumulated_usage)
        if result.structured_output is None:
            raise RuntimeError(f"{output_model.__name__} was not returned by {self.model_id}")
        return result.structured_output
