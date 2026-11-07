# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os


def get_text_content_location(file_bucket, file_key):
    file_text_content_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_content_bucket_name, file_key + ".content.json"]


def get_text_hash_content_location(file_bucket, dochash, namespace="global"):
    """Return the canonical extracted-text location for a content hash.

    Legacy callers retain the ``global/`` default. New ingestion can request
    ``global2/`` without changing hash-table, object-access, or progress-table
    identity semantics.
    """
    if namespace not in {"global", "global2"}:
        raise ValueError(f"Unsupported text namespace: {namespace}")
    file_text_content_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_content_bucket_name, f"{namespace}/{dochash}.content.json"]


def get_text_metadata_location(file_bucket, file_key):
    file_text_metadata_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_metadata_bucket_name, file_key + ".metadata.json"]
