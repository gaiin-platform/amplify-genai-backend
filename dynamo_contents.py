import boto3
import json
from pathlib import Path
from decimal import Decimal
from botocore.exceptions import NoCredentialsError
from boto3.dynamodb.types import TypeDeserializer

deserializer = TypeDeserializer()

def deserialize_item(item):
    return {k: deserializer.deserialize(v) for k, v in item.items()}

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        elif isinstance(obj, set):
            return list(obj)
        return super(DecimalEncoder, self).default(obj)

def get_all_dynamodb_tables(region_name='us-east-1', profile_name='default'):
    session = boto3.Session(profile_name=profile_name)
    client = session.client('dynamodb', region_name=region_name)

    table_names = []
    paginator = client.get_paginator('list_tables')
    for page in paginator.paginate():
        table_names.extend(page['TableNames'])

    return table_names, session

def get_table_items(table_name, session, region_name='us-east-1'):
    dynamodb = session.resource('dynamodb', region_name=region_name)
    table = dynamodb.Table(table_name)

    response = table.scan()
    items = response.get('Items', [])

    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))

    return items

if __name__ == "__main__":
    region = 'us-east-1'   # Replace with your region
    profile = 'default'    # Replace with your AWS profile

    print("🔍 Getting DynamoDB tables...")
    try:
        tables, session = get_all_dynamodb_tables(region, profile)
    except NoCredentialsError:
        print("❌ AWS credentials not found. Check your ~/.aws/credentials file.")
        exit(1)

    print(f"✅ Found {len(tables)} tables in region '{region}'")

    md_output = Path("dynamodb_contents.md")
    with md_output.open("w") as f:
        index = 1
        for table_name in tables:
            # print(f"\n*** {table_name} ***\n")
            f.write(f"\n*** {index}. {table_name} ***\n\n")

            items = get_table_items(table_name, session, region)
            if not items:
                # print("- (no items)")
                f.write("- (no items)\n\n")
                index = index + 1
                continue

            for item in items:
                item_str = json.dumps(item, indent=2, cls=DecimalEncoder)
                f.write(f"```\n{item_str}\n```\n")

            # print("-" * 40)
            f.write("-" * 40)
            index = index + 1

    print(f"\n✅ All table contents written to {md_output.resolve()}")
