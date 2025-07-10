import os
import json
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
admin_table = dynamodb.Table(os.environ.get("AMPLIFY_ADMIN_DYNAMODB_TABLE"))
s3_client = boto3.client("s3")
output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

PPTX_TEMPLATES = "powerPointTemplates"


def handle_pptx_upload(event, context):
    records = event.get("Records", [])
    for record in records:
        s3_info = record.get("s3", {})
        bucket_name = s3_info.get("bucket", {}).get("name")
        object_key = s3_info.get("object", {}).get("key")

        if not bucket_name or not object_key:
            print("Skipping record: missing bucket_name or object_key.")
            continue

        # Make sure this is in the templates/ prefix
        if not object_key.startswith("templates/"):
            print(f"Skipping record: object {object_key} is not in templates/ prefix.")
            continue

        template_name = object_key.replace("templates/", "")

        try:
            obj_head = s3_client.head_object(Bucket=bucket_name, Key=object_key)
        except ClientError as e:
            print(f"Could not find object {object_key} in {bucket_name}: {str(e)}")
            continue

        # Extract metadata
        metadata = obj_head.get("Metadata", {})
        is_available_str = metadata.get("isavailable", "true").lower()
        is_available = True if is_available_str == "true" else False

        # Split amplifygroups by comma; if empty string, results in [''], so filter empties
        amplify_groups_str = metadata.get("amplifygroups", "")
        amplify_groups = (
            [grp for grp in amplify_groups_str.split(",") if grp.strip()]
            if amplify_groups_str
            else []
        )

        # Retrieve existing templates
        config_item = admin_table.get_item(Key={"config_id": PPTX_TEMPLATES})
        if "Item" in config_item:
            existing_templates = config_item["Item"]["data"]
        else:
            # If not present, initialize as empty
            existing_templates = []

        # Convert list to dict for easy update
        existing_templates_dict = {t["name"]: t for t in existing_templates}

        existing_templates_dict[template_name] = {
            "name": template_name,
            "isAvailable": is_available,
            "amplifyGroups": amplify_groups,
        }

        # Save updated templates
        updated_templates = list(existing_templates_dict.values())
        try:
            admin_table.put_item(
                Item={
                    "config_id": PPTX_TEMPLATES,
                    "data": updated_templates,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(f"Template '{template_name}' registered successfully in DynamoDB.")
        except Exception as e:
            print(f"Error registering template '{template_name}': {str(e)}")

    return {"status": "done"}
