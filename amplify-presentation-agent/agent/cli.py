"""Run the presentation pipeline locally against a template directory.

Needs AWS credentials with Bedrock access, and LibreOffice for rendering
(or run inside the agent container). Examples:

  python cli.py generate --templates ../../misc_deployment_files/templates \
      --template celestial.pptx --content sample.md --out ./out
  python cli.py induct --templates ../../misc_deployment_files/templates --template celestial.pptx --out ./out
"""

import argparse
import json
import logging
import uuid

from presentation_agent.images import make_image_generator
from presentation_agent.induct import induct_template
from presentation_agent.pipeline import GenerateRequest, generate
from presentation_agent.stores import ConsoleJobStore, LocalBlobStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["generate", "induct"])
    parser.add_argument("--templates", required=True, help="Directory containing .pptx templates")
    parser.add_argument("--template", required=True, help="Template file name")
    parser.add_argument("--out", default="./out", help="Output directory (also caches induced catalogs)")
    parser.add_argument("--content", help="Markdown/text file with the source material")
    parser.add_argument("--title")
    parser.add_argument("--instructions")
    parser.add_argument("--model")
    parser.add_argument("--vision-model")
    parser.add_argument("--image-model", help="e.g. amazon.nova-canvas-v1:0")
    parser.add_argument("--passes", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    blobs = LocalBlobStore(args.out, template_dir=args.templates)
    if args.command == "induct":
        outcome = induct_template(args.template, blobs, args.model)
        print(json.dumps(outcome["usage"], indent=2))
        return

    with open(args.content) as f:
        content = f.read()
    job_id = f"local-{uuid.uuid4().hex[:8]}"
    request = GenerateRequest(
        job_id=job_id,
        user="local",
        template_name=args.template,
        content=content,
        title=args.title,
        instructions=args.instructions,
        model_id=args.model,
        vision_model_id=args.vision_model,
        image_model_id=args.image_model,
        max_review_passes=args.passes,
    )
    image_generator = make_image_generator(args.image_model) if args.image_model else None
    result = generate(request, blobs, ConsoleJobStore(), image_generator=image_generator)
    print(json.dumps(result.to_dict(), indent=2))
    print(json.dumps(result.usage, indent=2))


if __name__ == "__main__":
    main()
