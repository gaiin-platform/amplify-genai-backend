import os
import re
import uuid

import boto3
from botocore.client import Config
from pycommon.logger import getLogger

logger = getLogger("o365.attachment_staging")

# How long a staged-attachment download URL stays valid. Objects themselves
# are cleaned up by the staging bucket's lifecycle rule (1 day).
DOWNLOAD_URL_TTL_SECONDS = 900  # 15 minutes


class AttachmentStagingError(Exception):
    """Raised when an attachment cannot be staged for download."""

    pass


def stage_attachment_for_download(content: bytes, name: str, content_type: str) -> str:
    """
    Uploads attachment bytes to the staging bucket and returns a pre-signed GET
    URL the caller can fetch WITHOUT any auth header.

    Attachments over the direct-response size limit can't be returned through
    API Gateway (10MB response limit), and the raw Graph $value URL is useless
    to callers — it requires OUR Graph access token, which they don't have.
    Staging in S3 gives them a URL that actually works.

    Args:
        content: Raw attachment bytes fetched from the Graph API
        name: Attachment filename (sanitized for the S3 key)
        content_type: MIME type to serve the object with

    Returns:
        Pre-signed GET URL valid for DOWNLOAD_URL_TTL_SECONDS

    Raises:
        AttachmentStagingError: If the staging bucket is not configured
    """
    bucket = os.getenv("ATTACHMENT_STAGING_BUCKET")
    if not bucket:
        raise AttachmentStagingError(
            "ATTACHMENT_STAGING_BUCKET is not configured — cannot deliver "
            "attachments larger than the direct-response limit"
        )

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name or "")[-128:] or "attachment"
    key = f"staged-attachments/{uuid.uuid4().hex}/{safe_name}"

    # SigV4 explicitly — boto3's default presigned URLs are legacy SigV2,
    # which newer buckets/regions reject.
    s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
    )
    logger.info("Staged attachment %s (%d bytes) to s3://%s/%s", safe_name, len(content), bucket, key)
    return url
