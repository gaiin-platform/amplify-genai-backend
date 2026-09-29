"""Optional slide imagery via Amazon Nova Canvas on Bedrock."""

import base64
import json

import boto3

from .stores import aws_region

NEGATIVE_PROMPT = "text, words, letters, watermark, logo, signature, blurry, distorted"


def make_image_generator(model_id: str, region: str = None):
    client = boto3.client("bedrock-runtime", region_name=region or aws_region())

    def generate(prompt: str) -> bytes:
        body = {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {"text": prompt[:1000], "negativeText": NEGATIVE_PROMPT},
            "imageGenerationConfig": {"numberOfImages": 1, "width": 1280, "height": 720, "quality": "standard"},
        }
        response = client.invoke_model(modelId=model_id, body=json.dumps(body))
        payload = json.loads(response["body"].read())
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return base64.b64decode(payload["images"][0])

    return generate
