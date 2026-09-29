"""Structured deck plan produced by the planner and revised by the reviewer.

The builder is a pure function of (template, DeckPlan), so every revision the
reviewer makes is expressed as an edited plan rather than as edits to the
.pptx package. Slide-plan fields are adapted from the planning schema in
aws-samples/sample-strands-agent-with-agentcore (MIT) and the functional slide
types from icip-cas/PPTAgent (MIT). See THIRD_PARTY_NOTICES.md.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SlideFunction = Literal[
    "opening",
    "agenda",
    "section",
    "content",
    "comparison",
    "data",
    "process",
    "quote",
    "big_number",
    "closing",
]


class Bullet(BaseModel):
    text: str = Field(description="One concise point. No trailing period for fragments.")
    level: int = Field(default=0, ge=0, le=2, description="Indent level: 0 top, 1 sub-point, 2 detail.")


class Column(BaseModel):
    heading: Optional[str] = Field(default=None, description="Short column heading.")
    bullets: List[Bullet] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str
    values: List[float]


class ChartSpec(BaseModel):
    type: Literal["bar", "column", "stacked_column", "line", "pie", "doughnut"] = "column"
    categories: List[str]
    series: List[ChartSeries]
    number_format: Optional[str] = Field(default=None, description="Excel number format, e.g. '0%' or '$#,##0'.")
    title: Optional[str] = None


class TableSpec(BaseModel):
    headers: List[str]
    rows: List[List[str]]


class DiagramStep(BaseModel):
    label: str = Field(description="1-4 words.")
    detail: Optional[str] = Field(default=None, description="Optional short supporting phrase, under 12 words.")


class DiagramSpec(BaseModel):
    kind: Literal["process", "timeline"] = "process"
    steps: List[DiagramStep] = Field(min_length=2, max_length=6)


class ImageSpec(BaseModel):
    prompt: str = Field(description="Text-to-image prompt describing a concrete, non-text visual.")
    alt: str = Field(description="Alternative text for accessibility.")


class BigNumber(BaseModel):
    value: str = Field(description="The headline figure, e.g. '42%' or '$3.1M'.")
    label: str = Field(description="What the figure measures, under 12 words.")


class Quote(BaseModel):
    text: str
    attribution: Optional[str] = None


class SlideSpec(BaseModel):
    function: SlideFunction
    layout: int = Field(description="Index of a layout from the template catalog.")
    title: str = Field(description="Takeaway sentence (or literal title for opening/section/closing).")
    subtitle: Optional[str] = None
    bullets: List[Bullet] = Field(default_factory=list)
    columns: List[Column] = Field(default_factory=list, description="Exactly two for comparison slides.")
    chart: Optional[ChartSpec] = None
    table: Optional[TableSpec] = None
    diagram: Optional[DiagramSpec] = None
    image: Optional[ImageSpec] = None
    big_number: Optional[BigNumber] = None
    quote: Optional[Quote] = None
    source: Optional[str] = Field(default=None, description="Citation for data or claims on the slide.")
    notes: str = Field(default="", description="Speaker notes: what to say for this slide.")


class DeckPlan(BaseModel):
    title: str
    subtitle: Optional[str] = None
    audience: str
    objective: str
    narrative: List[str] = Field(description="The storyline as 3-6 short beats.")
    slides: List[SlideSpec]


class SlideIssue(BaseModel):
    slide: int = Field(description="1-based slide number.")
    severity: Literal["high", "medium", "low"]
    problem: str


class SlideRevision(BaseModel):
    slide: int = Field(description="1-based slide number being replaced.")
    spec: SlideSpec


class ReviewResult(BaseModel):
    score: int = Field(ge=1, le=10, description="Overall deck quality, 10 = ready to present.")
    issues: List[SlideIssue] = Field(default_factory=list)
    revisions: List[SlideRevision] = Field(
        default_factory=list,
        description="Full replacement specs for slides that must change. Omit slides that are fine.",
    )

    def blocking(self) -> List[SlideIssue]:
        return [i for i in self.issues if i.severity in ("high", "medium")]
