# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os


def get_text_content_location(file_bucket, file_key):
    file_text_content_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_content_bucket_name, file_key + ".content.json"]


def get_text_hash_content_location(file_bucket, dochash):
    file_text_content_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_content_bucket_name, "global/" + dochash + ".content.json"]


def get_text_metadata_location(file_bucket, file_key):
    file_text_metadata_bucket_name = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
    return [file_text_metadata_bucket_name, file_key + ".metadata.json"]
