"""Deck generation pipeline: plan -> (build -> render -> review -> revise)* -> finalize."""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .builder import build_deck
from .catalog import TemplateCatalog, build_catalog
from .llm import LLM, UsageTracker, image_block, text_block
from .plan import DeckPlan, ReviewResult
from .prompts import PLANNER_SYSTEM, REVIEWER_SYSTEM
from .render import montage, render_slides, thumbnail
from .stores import BlobStore, JobStore, catalog_key, output_prefix
from .validate import validate_deck

logger = logging.getLogger("presentation_agent.pipeline")

MAX_SOURCE_CHARS = 150_000
MAX_REVIEW_IMAGES = 12
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass
class GenerateRequest:
    job_id: str
    user: str
    template_name: str
    content: str
    title: Optional[str] = None
    instructions: Optional[str] = None
    model_id: Optional[str] = None
    vision_model_id: Optional[str] = None
    image_model_id: Optional[str] = None
    max_review_passes: int = 2


@dataclass
class GenerateResult:
    pptx_key: str
    slide_keys: List[str]
    slide_count: int
    title: str
    review_scores: List[int]
    usage: Dict[str, Dict[str, int]]
    images_generated: int
    duration_seconds: float
    warnings_remaining: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "pptxKey": self.pptx_key,
            "slideKeys": self.slide_keys,
            "slideCount": self.slide_count,
            "title": self.title,
            "reviewScores": self.review_scores,
            "imagesGenerated": self.images_generated,
            "durationSeconds": round(self.duration_seconds, 1),
            "warningsRemaining": self.warnings_remaining,
        }


def load_catalog(blobs: BlobStore, template_name: str, template: bytes) -> TemplateCatalog:
    """Prefer the induced (model-enriched) catalog; fall back to deterministic analysis."""
    cached = blobs.get_json(catalog_key(template_name))
    if cached:
        try:
            return TemplateCatalog.from_dict(cached)
        except (KeyError, TypeError) as e:
            logger.warning("Ignoring unreadable catalog for %s: %s", template_name, e)
    return build_catalog(template)


def planning_prompt(req: GenerateRequest, catalog: TemplateCatalog) -> str:
    content = req.content[:MAX_SOURCE_CHARS]
    parts = [
        "## Template catalog",
        catalog.prompt_summary(),
        "",
        f"Images enabled: {'yes' if req.image_model_id else 'no'}",
    ]
    if req.title:
        parts += ["", f"## Requested deck title\n{req.title}"]
    if req.instructions:
        parts += ["", f"## Instructions from the user\n{req.instructions}"]
    parts += ["", "## Source material", content]
    return "\n".join(parts)


def review_content(plan: DeckPlan, catalog: TemplateCatalog, warnings, slides: List[bytes]) -> List[Dict]:
    blocks = [
        text_block(
            "## Template catalog\n"
            + catalog.prompt_summary()
            + "\n\n## Current DeckPlan\n"
            + plan.model_dump_json(indent=1, exclude_none=True)
            + "\n\n## Lint warnings\n"
            + (json.dumps([w.to_dict() for w in warnings], indent=1) if warnings else "none")
            + "\n\n## Contact sheet of all slides"
        ),
        image_block(montage(slides, columns=4, tile_width=360)),
    ]
    flagged = {w.slide for w in warnings}
    if len(slides) <= MAX_REVIEW_IMAGES:
        chosen = list(range(1, len(slides) + 1))
    else:
        chosen = sorted(flagged)[:MAX_REVIEW_IMAGES]
        chosen += [n for n in range(1, len(slides) + 1) if n not in chosen][: MAX_REVIEW_IMAGES - len(chosen)]
        chosen.sort()
    for number in chosen:
        blocks.append(text_block(f"Slide {number}"))
        blocks.append(image_block(slides[number - 1]))
    return blocks


def apply_revisions(plan: DeckPlan, review: ReviewResult) -> int:
    applied = 0
    for revision in review.revisions:
        index = revision.slide - 1
        if 0 <= index < len(plan.slides):
            plan.slides[index] = revision.spec
            applied += 1
    return applied


def generate(
    req: GenerateRequest,
    blobs: BlobStore,
    jobs: JobStore,
    renderer: Callable[[bytes], List[bytes]] = render_slides,
    image_generator: Optional[Callable[[str], bytes]] = None,
    usage: Optional[UsageTracker] = None,
) -> GenerateResult:
    """Run a job end to end. Pass `usage` to keep token counts even if the job fails."""
    started = time.monotonic()
    usage = usage if usage is not None else UsageTracker()

    def progress(stage: str, pct: int, message: str) -> None:
        jobs.update(req.job_id, status="running", stage=stage, progress=pct, message=message)

    progress("loading", 5, "Loading template")
    template = blobs.get_template(req.template_name)
    catalog = load_catalog(blobs, req.template_name, template)

    progress("planning", 12, "Planning the storyline and slides")
    planner = LLM(req.model_id, usage)
    plan = planner.structured(PLANNER_SYSTEM, planning_prompt(req, catalog), DeckPlan)
    if not plan.slides:
        raise RuntimeError("The planner returned an empty deck")

    images: Dict[int, bytes] = {}
    if image_generator:
        wanted = [(i, s.image) for i, s in enumerate(plan.slides) if s.image]
        for n, (i, spec) in enumerate(wanted, start=1):
            progress("images", 25, f"Generating image {n} of {len(wanted)}")
            try:
                images[i] = image_generator(spec.prompt)
            except Exception as e:  # images are optional polish, never fatal
                logger.warning("Image generation failed for slide %d: %s", i + 1, e)

    reviewer = LLM(req.vision_model_id or req.model_id, usage)
    scores: List[int] = []
    passes = max(0, req.max_review_passes)
    deck = b""
    slides: List[bytes] = []
    warnings = []
    for attempt in range(passes + 1):
        span = 50 / (passes + 1)
        base = 30 + int(attempt * span)
        progress("building", base, f"Building {len(plan.slides)} slides" + (f" (revision {attempt})" if attempt else ""))
        deck = build_deck(template, catalog, plan, images)
        warnings = validate_deck(deck, catalog)
        progress("rendering", base + int(span * 0.2), "Rendering slides for review")
        slides = renderer(deck)
        if attempt == passes:
            break
        progress("reviewing", base + int(span * 0.4), f"Reviewing slides (pass {attempt + 1} of {passes})")
        review = reviewer.structured(REVIEWER_SYSTEM, review_content(plan, catalog, warnings, slides), ReviewResult)
        scores.append(review.score)
        logger.info("Review pass %d: score=%d issues=%d revisions=%d", attempt + 1, review.score, len(review.issues), len(review.revisions))
        if not review.blocking() and not review.revisions:
            break
        if apply_revisions(plan, review) == 0:
            break
        # Images are keyed by slide index; drop any whose slide no longer asks for one.
        images = {i: img for i, img in images.items() if i < len(plan.slides) and plan.slides[i].image}

    progress("finalizing", 92, "Saving presentation")
    prefix = output_prefix(req.user, req.job_id)
    pptx_key = f"{prefix}{_safe_filename(req.title or plan.title)}.pptx"
    blobs.put(pptx_key, deck, PPTX_MIME)
    slide_keys = []
    for number, png in enumerate(slides, start=1):
        key = f"{prefix}slide-{number:02d}.jpg"
        blobs.put(key, thumbnail(png), "image/jpeg")
        slide_keys.append(key)

    return GenerateResult(
        pptx_key=pptx_key,
        slide_keys=slide_keys,
        slide_count=len(slides),
        title=plan.title,
        review_scores=scores,
        usage=usage.as_dict(),
        images_generated=len(images),
        duration_seconds=time.monotonic() - started,
        warnings_remaining=[w.to_dict() for w in warnings],
    )


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip()
    return (cleaned or "presentation")[:80].replace(" ", "_")
