import base64
from datetime import datetime
import json
import math
import os
import boto3
from PIL import Image
from io import BytesIO
import urllib.parse
from rag.core import update_object_permissions
from pycommon.const import IMAGE_FILE_TYPES

s3 = boto3.client("s3")


def is_base64_image(file_content):
    """
    Check if the file content is a base64-encoded image.
    Returns True if it appears to be a valid base64 image, False otherwise.
    """
    try:
        # Try to decode as base64
        decoded_content = base64.b64decode(file_content)

        # Try to open as an image with PIL
        image = Image.open(BytesIO(decoded_content))
        image.verify()  # Verify it's a valid image

        return True
    except Exception:
        # If any step fails, it's not a valid base64 image
        return False


# currently is resized to support both clause and openAIs needs
def resize_image(image):
    min_edge_size = 200
    max_long_edge = 1568
    max_short_edge = 768

    width, height = image.size
    print(f"Original image size: {width}x{height}")

    # Check if resizing up is needed
    if width < min_edge_size or height < min_edge_size:
        print("Sizing image up")
        scale_factor = max(min_edge_size / width, min_edge_size / height)
        width = int(width * scale_factor)
        height = int(height * scale_factor)
        image = image.resize((width, height), Image.LANCZOS)

    # Check if resizing down is needed
    if (
        width > max_long_edge
        or height > max_long_edge
        or width > max_short_edge
        or height > max_short_edge
    ):
        print("Sizing image down")
        if width > height:
            # Landscape
            scale_factor = min(max_long_edge / width, max_short_edge / height)
        else:
            # Portrait
            scale_factor = min(max_long_edge / height, max_short_edge / width)

        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        print("Files new size: ", new_width, ", ", new_height)
        image = image.resize((new_width, new_height), Image.LANCZOS)

    return image


def cal_total_tokens_claude(width, height):
    # Claude - estimate the number of tokens used through this algorithm: tokens = (width px * height px)/750
    tokens = (width * height) / 750
    return math.ceil(tokens)


def cal_total_tokens_gpt(width, height):
    # GPT token calculation for high resolution only
    # The image should already be resized to fit within the required constraints
    # Calculate the number of 512px tiles
    num_tiles = math.ceil(width / 512) * math.ceil(height / 512)

    tokens = (num_tiles * 170) + 85
    return tokens


def process_images_for_chat(event, context):
    try:
        # Extract the bucket name and key from the event
        bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
        file_key = urllib.parse.unquote(event["Records"][0]["s3"]["object"]["key"])

        # Get object metadata to retrieve the ContentType
        head_response = s3.head_object(Bucket=bucket_name, Key=file_key)
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        content_type = head_response["ContentType"]

        # Handle different content type scenarios
        if content_type == "text/plain":
            # Check if this is a base64-encoded image (already processed)
            file_content = response["Body"].read()
            if is_base64_image(file_content):
                print(
                    f"File {file_key} is already a base64-encoded image, skipping processing"
                )
                return {
                    "statusCode": 200,
                    "body": json.dumps("Already processed base64 image, skipping"),
                }
            else:
                # This is a text/plain file that's not a base64 image - invalid for image processing
                raise Exception(
                    f"Invalid file type for image processing: {content_type}. File appears to be plain text, not an image."
                )

        elif content_type in IMAGE_FILE_TYPES:
            # This is a new image file that needs processing
            print(
                f"Processing new image file: {file_key} with content type: {content_type}"
            )
            image_data = response["Body"].read()
        else:
            # This is an unsupported file type
            raise Exception(
                f"Unsupported file type for image processing: {content_type}. Supported types: {IMAGE_FILE_TYPES}"
            )

        image = resize_image(Image.open(BytesIO(image_data)))

        print("Get entry in Files Dynamo Table using key ", file_key)
        dynamodb = boto3.resource("dynamodb")
        files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])

        response = files_table.get_item(Key={"id": file_key})
        item = response.get("Item", {})

        if not item:
            raise Exception(
                f"File data for for object {file_key} not found in DynamoDB"
            )

        name = item.get("name", "Unknown")
        tags = item.get("tags", [])
        file_type = item.get("type", "Unknown")
        createdAt = item.get("createdAt", datetime.now().isoformat())

        # Convert image to base64
        print("Convert image to base64")
        buffered = BytesIO()

        image.save(buffered, format=file_type.split("/")[1].upper())
        encoded_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # Save the base64 encoded image back to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=file_key,
            Body=encoded_image,
            ContentType="text/plain",
        )

        print(f"Base64 encoded image saved as {file_key}")

        # update permissions
        print("Update permissions")
        user = file_key.split("/")[0]

        permissions_update = {
            "dataSources": [file_key],
            "emailList": [user],
            "permissionLevel": "write",
            "policy": "",
            "principalType": "user",
            "objectType": "datasource",
        }
        update_object_permissions(user, permissions_update)

        # Update metadata
        image_metadata = put_image_file_metadata(
            bucket_name, file_key, name, tags, image.size, createdAt
        )

        try:
            files_table.update_item(
                Key={"id": file_key},
                UpdateExpression="SET totalTokens = :totalTokens",
                ExpressionAttributeValues={
                    ":totalTokens": image_metadata["totalTokens"]
                },
            )
            print(f"Updated file total tokens for {file_key}")

        except Exception as e:
            print(f"Error updating file total tokens for {file_key}: {str(e)}")
            # continue

        return {
            "statusCode": 200,
            "body": json.dumps("Image processed and saved as base64"),
        }

    except Exception as e:
        print(f"Error processing image: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(f"Error processing image: {str(e)}"),
        }


def put_image_file_metadata(bucket_name, key, name, tags, image_size, createdAt):
    width, height = image_size
    metadata_key = key + ".metadata.json"
    image_metadata = {
        "name": name,
        "contentKey": key,
        "createdAt": createdAt,
        "totalTokens": {
            "claude": cal_total_tokens_claude(width, height),
            "gpt": cal_total_tokens_gpt(width, height),
        },
        "tags": tags,
        "height": height,
        "width": width,
        "isImage": True,
    }
    print("Image metadata: ", image_metadata)

    s3.put_object(Bucket=bucket_name, Key=metadata_key, Body=json.dumps(image_metadata))
    print(f"Uploaded metadata to {bucket_name}/{metadata_key}")

    return image_metadata
