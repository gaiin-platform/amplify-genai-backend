"""Storage and job-status adapters (S3/DynamoDB in AgentCore, local files for the CLI)."""

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

TEMPLATE_PREFIX = "powerPointTemplates/"
LEGACY_TEMPLATE_PREFIX = "templates/"
CATALOG_PREFIX = "presentationAgent/catalogs/"
OUTPUT_PREFIX = "presentations/"


def aws_region() -> str:
    """Deployment region from the environment (AgentCore/Lambda set these); us-east-1 only for bare local runs."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalog_key(template_name: str) -> str:
    return f"{CATALOG_PREFIX}{template_name}.json"


def output_prefix(user: str, job_id: str) -> str:
    return f"{OUTPUT_PREFIX}{user}/{job_id}/"


class BlobStore:
    def get(self, key: str) -> Optional[bytes]:
        raise NotImplementedError

    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def get_template(self, name: str) -> bytes:
        data = self.get(TEMPLATE_PREFIX + name)
        if data is None:
            raise FileNotFoundError(f"PowerPoint template not found: {name}")
        return data

    def get_json(self, key: str) -> Optional[Dict]:
        data = self.get(key)
        return json.loads(data) if data else None

    def put_json(self, key: str, value: Dict) -> None:
        self.put(key, json.dumps(value, indent=2).encode(), "application/json")


class S3BlobStore(BlobStore):
    def __init__(self, bucket: str, legacy_template_bucket: Optional[str] = None):
        self.bucket = bucket
        self.legacy_template_bucket = legacy_template_bucket
        self.s3 = boto3.client("s3", region_name=aws_region())

    def _get(self, bucket: str, key: str) -> Optional[bytes]:
        try:
            return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "AccessDenied"):
                return None
            raise

    def get(self, key: str) -> Optional[bytes]:
        return self._get(self.bucket, key)

    def get_template(self, name: str) -> bytes:
        # Same lookup order as amplify-lambda/converters/docconverter.py.
        data = self.get(TEMPLATE_PREFIX + name)
        if data is None and self.legacy_template_bucket:
            data = self._get(self.legacy_template_bucket, LEGACY_TEMPLATE_PREFIX + name)
        if data is None:
            raise FileNotFoundError(f"PowerPoint template not found: {name}")
        return data

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)


class LocalBlobStore(BlobStore):
    """Maps keys onto a directory; templates may also be read from a flat template dir."""

    def __init__(self, root: str, template_dir: Optional[str] = None):
        self.root = root
        self.template_dir = template_dir

    def get(self, key: str) -> Optional[bytes]:
        path = os.path.join(self.root, key)
        if not os.path.exists(path) and self.template_dir and key.startswith(TEMPLATE_PREFIX):
            path = os.path.join(self.template_dir, key[len(TEMPLATE_PREFIX):])
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


class JobStore:
    def update(self, job_id: str, **fields) -> None:
        raise NotImplementedError


class DynamoJobStore(JobStore):
    def __init__(self, table_name: str):
        self.table = boto3.resource("dynamodb", region_name=aws_region()).Table(table_name)

    def update(self, job_id: str, **fields) -> None:
        fields["updatedAt"] = _now()
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": _to_dynamo(v) for k, v in fields.items()}
        expression = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
        self.table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )


class ConsoleJobStore(JobStore):
    def __init__(self):
        self.started = time.monotonic()
        self.state: Dict = {}

    def update(self, job_id: str, **fields) -> None:
        self.state.update(fields)
        elapsed = time.monotonic() - self.started
        summary = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "result")
        print(f"[{elapsed:6.1f}s] {job_id}: {summary}", flush=True)


def _to_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value
