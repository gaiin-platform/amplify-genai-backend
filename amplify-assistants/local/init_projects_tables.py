#!/usr/bin/env python3
"""Create the Projects tables in DynamoDB Local if they do not exist."""

import os

import boto3
from botocore.exceptions import ClientError


ENDPOINT = os.getenv("DYNAMODB_ENDPOINT_URL", "http://127.0.0.1:8000")
REGION = os.getenv("AWS_REGION", "us-east-1")
PROJECTS_TABLE = os.getenv("PROJECTS_DYNAMODB_TABLE", "amplify-projects-local")
FILES_TABLE = os.getenv("PROJECT_FILES_DYNAMODB_TABLE", "amplify-project-files-local")
MEMORIES_TABLE = os.getenv(
    "PROJECT_MEMORIES_DYNAMODB_TABLE", "amplify-project-memories-local"
)

dynamodb = boto3.client(
    "dynamodb",
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="local",
    aws_secret_access_key="local",
)


def create_table_if_missing(name, attribute_definitions, key_schema, indexes):
    try:
        dynamodb.describe_table(TableName=name)
        print(f"Local table already exists: {name}")
        return
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    extra = {"GlobalSecondaryIndexes": indexes} if indexes else {}
    dynamodb.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=attribute_definitions,
        KeySchema=key_schema,
        **extra,
    )
    dynamodb.get_waiter("table_exists").wait(TableName=name)
    print(f"Created local table: {name}")


create_table_if_missing(
    PROJECTS_TABLE,
    [
        {"AttributeName": "id", "AttributeType": "S"},
        {"AttributeName": "createdBy", "AttributeType": "S"},
        {"AttributeName": "createdAt", "AttributeType": "S"},
    ],
    [{"AttributeName": "id", "KeyType": "HASH"}],
    [
        {
            "IndexName": "CreatedByIndex",
            "KeySchema": [
                {"AttributeName": "createdBy", "KeyType": "HASH"},
                {"AttributeName": "createdAt", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
)

create_table_if_missing(
    MEMORIES_TABLE,
    [
        {"AttributeName": "id", "AttributeType": "S"},
        {"AttributeName": "projectId", "AttributeType": "S"},
        {"AttributeName": "createdAt", "AttributeType": "S"},
    ],
    [{"AttributeName": "id", "KeyType": "HASH"}],
    [
        {
            "IndexName": "ProjectIdIndex",
            "KeySchema": [
                {"AttributeName": "projectId", "KeyType": "HASH"},
                {"AttributeName": "createdAt", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
)

create_table_if_missing(
    FILES_TABLE,
    [
        {"AttributeName": "projectId", "AttributeType": "S"},
        {"AttributeName": "fileId", "AttributeType": "S"},
    ],
    [
        {"AttributeName": "projectId", "KeyType": "HASH"},
        {"AttributeName": "fileId", "KeyType": "RANGE"},
    ],
    [],
)
