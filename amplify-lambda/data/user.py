import os
import re
import time
import uuid
from typing import List, Dict, Any

import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal
import json

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


class CommonData:
    def __init__(self, table_name):
        self.table = boto3.resource("dynamodb").Table(table_name)

    def _float_to_decimal(self, data):
        """Convert floats to Decimal in data structure"""
        return json.loads(json.dumps(data), parse_float=Decimal)

    def _decimal_to_float(self, data):
        """Convert Decimals back to floats in data structure"""
        return json.loads(json.dumps(data, cls=DecimalEncoder))

    def put_item(self, app_id, entity_type, item_id, data, range_key=None):
        sk = f"{item_id}#{range_key}" if range_key else item_id
        existing_item = self.get_item(app_id, entity_type, item_id, range_key)

        # If the item exists, retrieve the existing UUID
        uuid_key = str(uuid.uuid4()) if not existing_item else existing_item["UUID"]

        item = {
            "PK": f"{app_id}#{entity_type}",
            "SK": sk,
            "UUID": uuid_key,
            "data": self._float_to_decimal(data),
            "appId": app_id,
            "entityType": entity_type,
            "createdAt": int(time.time()),
        }

        if range_key:
            item["rangeKey"] = range_key

        # Put item with a condition to only overwrite if not exists
        self.table.put_item(Item=item)
        return uuid_key

    def get_by_uuid(self, uuid_key):
        """
        Get item directly by UUID using GSI
        """
        response = self.table.query(
            IndexName="UUID-index", KeyConditionExpression=Key("UUID").eq(uuid_key)
        )
        items = response.get("Items", [])
        if items:
            return self._decimal_to_float(items[0])
        return None

    def get_item(self, app_id, entity_type, item_id, range_key=None):
        sk = f"{item_id}#{range_key}" if range_key else item_id
        response = self.table.get_item(Key={"PK": f"{app_id}#{entity_type}", "SK": sk})
        item = response.get("Item")
        if item:
            return self._decimal_to_float(item)
        return None

    def query_by_range(
        self, app_id, entity_type, range_start=None, range_end=None, limit=100
    ):
        key_condition = Key("PK").eq(f"{app_id}#{entity_type}")

        if range_start and range_end:
            key_condition = key_condition & Key("SK").between(range_start, range_end)
        elif range_start:
            key_condition = key_condition & Key("SK").gte(range_start)
        elif range_end:
            key_condition = key_condition & Key("SK").lte(range_end)

        response = self.table.query(KeyConditionExpression=key_condition, Limit=limit)
        return self._decimal_to_float(response["Items"])

    def query_by_prefix(self, app_id, entity_type, prefix, limit=100):
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"{app_id}#{entity_type}")
            & Key("SK").begins_with(prefix),
            Limit=limit,
        )
        return self._decimal_to_float(response["Items"])

    def query_by_type(self, app_id, entity_type, limit=100):
        """
        Query all items of a specific entity_type for a given app_id.
        Args:
            app_id (str): The application ID.
            entity_type (str): The type of entity to query.
            limit (int): The maximum number of items to return (default is 100).
        Returns:
            list: A list of items matching the entity_type.
        """
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"{app_id}#{entity_type}"), Limit=limit
        )
        return self._decimal_to_float(response.get("Items", []))

    def delete_item(self, app_id, entity_type, item_id, range_key=None):
        sk = f"{item_id}#{range_key}" if range_key else item_id
        return self.table.delete_item(Key={"PK": f"{app_id}#{entity_type}", "SK": sk})

    def delete_by_uuid(self, uuid_key):
        """
        Delete item directly by UUID using GSI
        """
        # First, retrieve the item by UUID to get the PK and SK needed for deletion
        response = self.table.query(
            IndexName="UUID-index", KeyConditionExpression=Key("UUID").eq(uuid_key)
        )

        items = response.get("Items", [])

        if not items:
            return False  # No item found to delete

        # Assume we only delete the first matching item
        item_to_delete = items[0]
        pk = item_to_delete["PK"]
        sk = item_to_delete["SK"]

        # Delete the item
        self.table.delete_item(Key={"PK": pk, "SK": sk})

        return True

    def batch_put_items(self, app_id, entity_type, items):
        """
        Batch write items to DynamoDB
        items format: [{'itemId': 'id1', 'rangeKey': 'optional', 'data': {...}}, ...]
        """
        with self.table.batch_writer() as batch:
            all_uuids = []

            for item in items:

                sk = (
                    f"{item['itemId']}#{item['rangeKey']}"
                    if item.get("rangeKey")
                    else item["itemId"]
                )
                existing_item = self.get_item(
                    app_id, entity_type, item["itemId"], item.get("rangeKey")
                )

                uuid_key = (
                    str(uuid.uuid4()) if not existing_item else existing_item["UUID"]
                )

                batch_item = {
                    "PK": f"{app_id}#{entity_type}",
                    "SK": sk,
                    "UUID": uuid_key,
                    "data": self._float_to_decimal(item["data"]),
                    "appId": app_id,
                    "entityType": entity_type,
                    "createdAt": int(time.time()),
                }

                if item.get("rangeKey"):
                    batch_item["rangeKey"] = item["rangeKey"]

                batch.put_item(Item=batch_item)
                all_uuids.append(uuid_key)

        return all_uuids

    def batch_get_items(
        self, app_id: str, entity_type: str, item_ids: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        """
        Batch get items from DynamoDB.

        Args:
            app_id: Application identifier
            entity_type: Type of entity to fetch
            item_ids: List of dictionaries containing item IDs and optional range keys
                     Format: [{'itemId': 'id1', 'rangeKey': 'optional'}, ...]

        Returns:
            List of deserialized DynamoDB items

        Raises:
            Exception: If batch_get_item fails or returns an error
        """
        keys = [
            {
                "PK": {"S": f"{app_id}#{entity_type}"},
                "SK": {
                    "S": (
                        f"{item['itemId']}#{item['rangeKey']}"
                        if item.get("rangeKey")
                        else item["itemId"]
                    )
                },
            }
            for item in item_ids
        ]

        response_items = []
        deserializer = TypeDeserializer()
        dynamodb = boto3.client("dynamodb")

        for i in range(0, len(keys), 100):  # DynamoDB batch get limit is 100
            batch_keys = keys[i : i + 100]
            request_items = {self.table.name: {"Keys": batch_keys}}

            while request_items:
                batch_response = dynamodb.batch_get_item(RequestItems=request_items)

                # Process returned items
                if self.table.name in batch_response.get("Responses", {}):
                    items = batch_response["Responses"][self.table.name]
                    # Deserialize each item individually
                    deserialized_items = [
                        deserializer.deserialize({"M": item}) for item in items
                    ]
                    response_items.extend(deserialized_items)

                # Handle unprocessed items
                request_items = batch_response.get("UnprocessedKeys", {})
                if request_items:
                    # Add exponential backoff here if needed
                    print(f"Retrying {len(request_items)} unprocessed keys")

        return response_items

    def batch_delete_items(self, app_id, entity_type, item_ids):
        """
        Batch delete items from DynamoDB
        item_ids format: [{'itemId': 'id1', 'rangeKey': 'optional'}, ...]
        """

        with self.table.batch_writer() as batch:
            for item in item_ids:
                sk = (
                    f"{item['itemId']}#{item['rangeKey']}"
                    if item.get("rangeKey")
                    else item["itemId"]
                )
                batch.delete_item(Key={"PK": f"{app_id}#{entity_type}", "SK": sk})
        return True

    def list_app_ids(self, prefix=None):
        """
        List all unique appIds in the DynamoDB table that optionally start with a given prefix.

        Args:
            prefix (str, optional): If provided, only returns appIds that start with this prefix.

        Returns:
            list: A list of unique appIds, optionally filtered by prefix.
        """
        app_ids = set()

        # Initialize scanning parameters
        scan_params = {
            "ProjectionExpression": "PK",
            "FilterExpression": "begins_with(PK, :prefix)" if prefix else None,
            "ExpressionAttributeValues": {":prefix": f"{prefix}"} if prefix else None,
        }

        # Remove None values from scan_params
        scan_params = {k: v for k, v in scan_params.items() if v is not None}

        done = False
        start_key = None

        while not done:
            if start_key:
                scan_params["ExclusiveStartKey"] = start_key

            response = self.table.scan(**scan_params)

            # Extract appIds from PK values
            for item in response.get("Items", []):
                pk = item.get("PK", "")
                if "#" in pk:
                    app_id = pk
                    # If prefix is specified, double-check the filtering
                    # (DynamoDB FilterExpression matches against full PK)
                    if prefix:
                        if app_id.startswith(prefix):
                            app_ids.add(app_id)
                    else:
                        app_ids.add(app_id)

            # Check if there are more items to scan
            start_key = response.get("LastEvaluatedKey")
            done = start_key is None

        return sorted(list(app_ids))
