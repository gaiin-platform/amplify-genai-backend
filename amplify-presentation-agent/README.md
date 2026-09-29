# amplify-presentation-agent

Generates PowerPoint decks on **Amazon Bedrock AgentCore Runtime**, using the
admin-uploaded `.pptx` templates. It replaces the pandoc export for the new UI.
The old UI still uses `/chat/convert`.

```
New UI ─POST /presentation/start──► start_presentation (Lambda) ─► jobs table (queued)
   │                                         └─ invoke_agent_runtime (async) ─┐
   └─GET /presentation/status (poll) ◄── jobs table ◄── AgentCore Runtime ◄──┘
                                          plan → build → render → review → revise (≤N) → S3
Template upload (S3) → amplify-lambda handlePptxUpload → analyzeTemplateUpload → runtime "induct"
```

## Pipeline (`agent/presentation_agent`)

| Stage | Module | Notes |
|---|---|---|
| Template catalog | `catalog.py` | Deterministic: layouts, regions, text capacity, and theme fonts and colors |
| Template induction | `induct.py` | One-time per template. A vision model describes each layout and filters broken ones. Output goes to `presentationAgent/catalogs/<name>.json` |
| Plan | `pipeline.py`, `prompts.py`, `plan.py` | Structured `DeckPlan`: narrative, then per-slide layout, takeaway title, bullets/chart/table/diagram/image, and notes |
| Build | `builder.py` | Deterministic python-pptx. Fills the template's own placeholders and adds native charts, tables and diagrams |
| Validate | `validate.py` | Lint for overflow, word breaks, bounds, leftover placeholder text and density |
| Render | `render.py` | LibreOffice → PDF → PNG, plus a contact sheet |
| Review | `pipeline.py` | A vision model scores the rendered slides and returns replacement slide specs. The deck is rebuilt from the edited plan |

Because the builder is deterministic, a review only ever edits the plan.

## Configuration

The admin config `presentationAgent` lives in the admin table and is editable in the admin panel:

- `modelId`: planner model. Default is `us.anthropic.claude-opus-5`.
- `visionModelId`: reviewer and induction model. Defaults to `modelId`.
- `imageModelId`: optional, e.g. `amazon.nova-canvas-v1:0`. Leave empty to disable generated images.
- `maxReviewPasses`: 0–4. Default is 2.

Feature flag: `presentationAgent`. It controls whether the new UI shows the export.

## Deploy

1. `scripts/build_push_agent.sh <stage> [region]`. This builds the `linux/arm64` image and pushes it to ECR.
2. Set `PRESENTATION_AGENT_IMAGE_URI` in `var/<stage>-var.yml`.
3. `serverless deploy --stage <stage>`, run from this directory or through compose.

If `PRESENTATION_AGENT_IMAGE_URI` is empty, the API deploys without the runtime, and start requests return a clear error.

Template fonts: drop `.ttf`/`.otf` files into `agent/fonts/` so renders match PowerPoint.
Otherwise LibreOffice substitutes metric-compatible fonts.

## Local development

```bash
cd agent
uv venv .venv && uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest                      # no AWS needed

# Full run with Bedrock credentials (rendering needs LibreOffice; or use the container):
.venv/bin/python cli.py generate --templates ../../misc_deployment_files/templates \
    --template celestial.pptx --content notes.md --out ./out

# Container smoke test
docker build -t amplify-presentation-agent:local .
docker run -p 8080:8080 -e AWS_REGION=us-east-1 ... amplify-presentation-agent:local
curl localhost:8080/ping
```

Lambda tests: `uv venv .venv && uv pip install pycommon@git+https://github.com/gaiin-platform/pycommon.git@v0.1.2 boto3 pytest && .venv/bin/python -m pytest`.
