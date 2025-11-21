# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import hashlib
import os
import mimetypes
import boto3
import json
import urllib.parse
from datetime import datetime
import re
import traceback
from boto3.dynamodb.conditions import Key
import nltk
from nltk.tokenize import sent_tokenize
import asyncio

# Uncomment for local testing 
# import sys
# if __name__ == "__main__":
#     # Add the parent directory to Python path for local testing
#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     parent_dir = os.path.dirname(current_dir)  # Go up to amplify-lambda/
#     sys.path.insert(0, parent_dir)


from rag.handlers.commaseparatedvalues import CSVHandler
from rag.handlers.excel import ExcelHandler
from rag.handlers.pdf import PDFHandler
from rag.handlers.powerpoint import PPTXHandler
from rag.handlers.word import DOCXHandler
from rag.handlers.text import TextHandler
from rag.handlers.markdown import MarkDownHandler
from rag.handlers.markitdown_extractor import MarkItDownExtractor
from rag.util import (
    get_text_content_location,
    get_text_metadata_location,
    get_text_hash_content_location,
)
from rag.handlers.shared_functions import is_likely_text
from rag.rag_secrets import get_rag_secrets_for_document, delete_rag_secrets_for_document

s3 = boto3.client("s3")
sqs = boto3.client("sqs")


def decode_text(file_content, encoding):
    # Decode the file content using the detected encoding
    try:
        text = file_content.decode(encoding)
        return text
    except UnicodeDecodeError as e:
        # You can handle decoding errors differently if necessary
        raise ValueError(f"Decoding failed for encoding {encoding}: {e}")


def get_handler_and_split_params(key):
    if key.endswith(".pdf"):
        return PDFHandler(), {}
    elif key.endswith(".docx"):
        return DOCXHandler(), {}
    elif key.endswith(".pptx"):
        return PPTXHandler(), {}
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler(), {"min_chunk_size": 512}
    elif key.endswith(".csv"):
        return CSVHandler(), {"min_chunk_size": 512}
    elif key.endswith(".html"):
        return None  # HTMLHandler(), {}
    else:
        return TextHandler(), {}


def get_text_extraction_handler(key):
    if key.endswith(".pdf"):
        return PDFHandler()
    elif key.endswith(".docx"):
        return DOCXHandler()
    elif key.endswith(".pptx"):
        return PPTXHandler()
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler()
    elif key.endswith(".csv"):
        return CSVHandler()
    elif key.endswith(".md"):
        return MarkDownHandler()
    else:
        return TextHandler()


def get_file_extension(file_name, mime_type):
    # Attempt to guess the extension for the given mime type
    guessed_extension = mimetypes.guess_extension(mime_type)
    file_extension = os.path.splitext(file_name)[1]
    # If an extension for the mime type is found, return it
    if guessed_extension and guessed_extension.startswith("."):
        # Ensure there's a dot at the beginning of the extension
        return guessed_extension
    # If no extension is found for that mime type, return the original extension with "file." prepended
    elif guessed_extension:
        return "." + guessed_extension
    else:
        return file_extension


def preprocess_visual_content(file_content, key):
    """Extract visuals, replace with markers, return modified content + mapping"""
    if key.endswith(".pdf"):
        return PDFHandler().preprocess_pdf_visuals(file_content)
    elif key.endswith(".docx"):
        return DOCXHandler().preprocess_docx_visuals(file_content)
    elif key.endswith(".pptx"):
        return PPTXHandler().preprocess_pptx_visuals(file_content)
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler().preprocess_excel_visuals(file_content)
    else:
        # No visual processing for text files, etc.
        return file_content, {}


# Extract text from file and return an array of chunks
async def extract_text_from_file(key, file_content, current_user=None, account_data=None):
    # only need to process visuals if account_data is provided since it contains chat-js required data
    processed_content, visual_map = preprocess_visual_content(file_content, key) if account_data else (file_content, {})

    # Process visuals asynchronously if any exist
    if current_user and visual_map:
        print(f"Processing {len(visual_map)} visuals for {key}")
        try:
            # ! Import here to avoid circular imports
            from rag.handlers.visual_to_text import batch_process_visuals

            visual_map = await batch_process_visuals(visual_map, current_user, account_data)
            print(f"Visual processing completed: {len(visual_map)} successful transcriptions")
        except ImportError as e:
            print(f"Visual processing not available (circular import): {e}")
            print("Continuing without visual processing...")

    try:
        # First we will try with markitdown extractor,
        # if that fails then we will proceed as before
        markitdown_extractor = MarkItDownExtractor()
        markitdown_result = markitdown_extractor.extract_from_content(processed_content, key)
        if markitdown_result:
            print(f"MarkItDown extraction:\n\n {markitdown_result}")
            md_bytes = markitdown_result.encode('utf-8')
            print(f"MarkItDown extraction successful for {key}")
            return MarkDownHandler().extract_text(md_bytes, key, visual_map)

    except Exception as e:
        print(f"Unable to extract text from {key} using markitdown extractor: {str(e)}")
    print("Continuing with default handler logic...")

    # Get the appropriate handler and split parameters for the file type
    handler = get_text_extraction_handler(key)

    if handler:
        try: # using file_contents due to efficient location insertion, unlike markitdown which needs the altered preprocessed content
            return handler.extract_text(file_content, visual_map)
        except Exception as e:
            print(f"Error extracting text from {key}: {str(e)}")
            # Return an empty list to indicate that no chunks could be extracted
            return []
    else:
        is_text, encoding = is_likely_text(file_content)

        if is_text:
            # Decode file_content using the detected encoding
            text_content = decode_text(file_content, encoding)
            try:
                return TextHandler().extract_text(text_content, visual_map)
            except Exception as e:
                print(f"Error extracting text from {key}: {str(e)}")
                # Return an empty list to indicate that no chunks could be extracted
                return []


def split_text(sent_tokenize, content):
    if content.get("canSplit"):
        text = re.sub(r"\s+", " ", content["content"].strip())
        # Sentences will not need whitespace normalization.
        sentences = sent_tokenize(text)

        return [
            {**content, "content": sentence, "index": index}
            for index, sentence in enumerate(sentences)
        ]
    else:
        return [content]


def save_chunks(chunks_bucket, key, split_count, chunks, object_key=None, force_reprocess=False):
    print(f"Saving chunks {len(chunks)} to {chunks_bucket}/{key}-{split_count}.chunks.json")
    chunks_key = f"{key}-{split_count}.chunks.json"
    
    # Prepare metadata for S3 object
    metadata = {}
    if object_key:
        metadata["object_key"] = object_key
        print(f"Adding object_key metadata: {object_key}")
    
    if force_reprocess:
        metadata["force_reprocess"] = "true"
        print(f"Adding force_reprocess metadata: true")
    
    s3.put_object(
        Bucket=chunks_bucket,
        Key=chunks_key,
        Body=json.dumps({"chunks": chunks, "src": key}),
        Metadata=metadata
    )
    print(f"Uploaded chunks to {chunks_bucket}/{chunks_key}")
    if object_key:
        print(f"Stored object_key metadata: {object_key}")



def chunk_content(key, text_content, split_params, object_key=None, force_reprocess=False):
    # nltk.download('punkt')
    nltk.data.path.append("/tmp")
    nltk.download("punkt", download_dir="/tmp")
    # Normalize whitespace once at the start.

    chunks = []
    current_chunk = []
    current_chunk_size = 0
    char_index = 0
    content_index = 0
    min_chunk_size = split_params.get("min_chunk_size", 512)

    total_chunks = 0
    max_chunks = int(os.environ.get("MAX_CHUNKS", "1000"))

    parts = [split_text(sent_tokenize, item) for item in text_content["content"]]
    flattened_list = [item for sublist in parts for item in sublist]

    locations = []
    indexes = []

    chunks_bucket = os.environ["S3_RAG_CHUNKS_BUCKET_NAME"]
    split_increment = 10
    split_count = 0
    index = 0

    total_chunks = 0  # Initialize total_chunks

    for content_part in flattened_list:
        sentence = content_part["content"]
        location = content_part["location"]
        sentence_length = len(sentence)

        if total_chunks >= max_chunks:
            print(f"Reached maximum chunks {max_chunks} for {key}")
            break

        # Check if adding this sentence would exceed the chunk size.
        if (
            current_chunk
            and (current_chunk_size + sentence_length + 1) >= min_chunk_size
        ):
            # Join the current chunk with space and create the chunk object.
            chunk_text = " ".join(current_chunk)

            chunks.append(
                {
                    "content": chunk_text,
                    "locations": locations,
                    "indexes": indexes,
                    "char_index": char_index,
                }
            )

            # Reset for the next chunk
            locations = []
            indexes = []
            char_index += (
                len(chunk_text) + 1
            )  # Include the space that joins with the next chunk.
            current_chunk = [sentence]  # Start the new chunk with the current sentence.
            current_chunk_size = sentence_length

            total_chunks += 1  # Increment the count after forming a chunk

            if len(chunks) == split_increment:
                split_count += 1
                save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess)
                chunks = []

        else:
            locations.append(location)
            indexes.append(index)
            index += 1
            if current_chunk:
                current_chunk.append(sentence)
                current_chunk_size += sentence_length + 1
            else:
                current_chunk = [sentence]
                current_chunk_size = sentence_length

    # If there's remaining text in the current chunk, add it as the last chunk.
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        chunks.append(
            {
                "content": chunk_text,
                "locations": locations,
                "indexes": indexes,
                "char_index": char_index,
            }
        )
        total_chunks += 1  # Increment the count for the last chunk

    if chunks:  # If there are unfinished chunks, save them
        split_count += 1
        save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess)

    print(f"In Chunk Content Function")
    print(
        f"Split Count: {split_count}, Split Increment: {split_increment}, Total Chunks: {total_chunks}"
    )
    return split_count


def chunk_s3_file_content(bucket, key, object_key=None, force_reprocess=False):
    try:
        # Download the file from S3
        print(f"Fetching text from {bucket}/{key}")
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        data = s3_object["Body"].read()
        print(f"Fetched text from {bucket}/{key}")

        file_content = json.loads(data)
        print(f"Loaded json from {bucket}/{key}")

        # Extract text from the file in S3
        chunks = chunk_content(key, file_content, {}, object_key, force_reprocess)
        print(
            f"Chunk S3 File Content Function: Chunked content for {key} into {chunks} chunks"
        )

        return chunks

    except Exception as e:
        traceback.print_exc()
        print(f"Error getting object {key} from bucket {bucket}: {str(e)}")
        return None


def scan_directory_and_save_text(directory_path):
    # Iterate over all files in the given directory
    for filename in os.listdir(directory_path):
        # Skip directories, only process files
        if not os.path.isfile(os.path.join(directory_path, filename)):
            continue

        print(f"Processing file: {filename}")
        # Read the file content
        with open(os.path.join(directory_path, filename), "rb") as f:
            file_content = f.read()

        # Use the previously defined extract_text_from_file function to get chunks
        # Notice that this function might need to be adapted depending on the handlers implementations
        document_structure = asyncio.run(extract_text_from_file(filename, file_content))
        data = {
            "name": filename,
            "content": document_structure,
        }

        # Save the chunks to a file
        with open(os.path.join(directory_path, filename + ".content.json"), "w") as f:
            json.dump(data, f, indent=2)


def extract_text_from_s3_file(bucket, key, file_extension):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = s3_object["Body"].read()

        return asyncio.run(extract_text_from_file(file_extension, file_content))

    except Exception as e:
        print(f"Error getting object {key} from bucket {bucket}: {str(e)}")
        return None


def get_file_from_s3(bucket, key):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = s3_object["Body"].read()

        return file_content

    except Exception as e:
        print(f"Error getting object {key} from bucket {bucket}: {str(e)}")
        return None


# DEPRECATED: queue_document_for_rag
# Deprecated: queue_document_for_rag_chunking


def update_object_permissions(current_user, data):
    dynamodb = boto3.resource("dynamodb")
    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]

    try:
        data_sources = data["dataSources"]
        email_list = data["emailList"]
        provided_permission_level = data[
            "permissionLevel"
        ]  # Permission level provided for other users
        policy = data["policy"]  # No need to use get() since policy is always present
        principal_type = data.get("principalType")
        object_type = data.get("objectType")

        print(
            f"Updating permission on {data_sources} for {email_list} with {provided_permission_level} and {policy}"
        )

        # Get the DynamoDB table
        table = dynamodb.Table(table_name)

        for object_id in data_sources:
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("object_id").eq(
                    object_id
                )
            )
            items = query_response.get("Items")

            # If there are no permissions, create the initial item with the current_user as the owner
            if not items:
                table.put_item(
                    Item={
                        "object_id": object_id,
                        "principal_id": current_user,
                        "principal_type": principal_type,
                        "object_type": object_type,
                        "permission_level": "write",  # The current_user becomes the owner
                        "policy": policy,
                    }
                )
                print(
                    f"Created initial item for {object_id} with {current_user} as owner"
                )

            else:
                # If current_user is the owner or has write permission, proceed with updates
                for principal_id in email_list:
                    # Create or update the permission level for each principal_id
                    principal_key = {
                        "object_id": object_id,
                        "principal_id": principal_id,
                    }
                    # Use the provided permission level for other users
                    update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
                    expression_attribute_values = {
                        ":principal_type": principal_type,
                        ":object_type": object_type,
                        ":permission_level": provided_permission_level,  # Use the provided permission level
                        ":policy": policy,
                    }
                    table.update_item(
                        Key=principal_key,
                        UpdateExpression=update_expression,
                        ExpressionAttributeValues=expression_attribute_values,
                    )
                    print(
                        f"Updated item for {object_id} with {principal_id} to {provided_permission_level}"
                    )

    except Exception as e:
        print(f"Failed to update permissions: {str(e)}")
        return False

    print(
        f"Updated permissions for {data_sources} for {email_list} with {provided_permission_level} and {policy}"
    )
    return True



def process_document_for_rag(event, context):
    print(f"Received event: {event}")
    s3 = boto3.client("s3")

    dynamodb = boto3.resource("dynamodb")
    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    hash_files_table = dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"])

    for record in event["Records"]:
        try:
            print(f"Processing message: {record}")
            s3_event = json.loads(record["body"])
            print(f"Message body: {s3_event}")
            s3_record = s3_event["Records"][0] if "Records" in s3_event else s3_event
            s3_info = s3_record["s3"]

            # Check if this is a force reprocessing request
            force_reprocess = s3_record.get("force_reprocess", False)

            # Get the bucket and object key from the event
            print(f"Getting text from {s3_info['object']['key']}")
            bucket = s3_info["bucket"]["name"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            print(f"Bucket / Key {bucket} / {key}")

            user = None
            account_data = None
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                print(f"Response Metadata: {response['Metadata']}")
                rag_enabled = (
                    True
                    if force_reprocess
                    else response["Metadata"].get("rag_enabled", "false") == "true"
                )
               
                print("Retrive rag details from parameter store")
                rag_details = get_rag_secrets_for_document(key)
                if rag_details['success']:
                    account_data = rag_details['data']
                    user = account_data['user']
                else:
                    print("Failed to retrieve RAG details from parameter store")

            except Exception as e:
                print(f"Error fetching metadata for {key}: {str(e)}")

            response = files_table.get_item(Key={"id": key})

            # The rest is the same as above
            item = response.get("Item", None)

            if item:
                print(f"Found file entry for {key}: {item}")
            else:
                print(f"File entry not found for {key}")

            total_items = 0
            text = None
            location_properties = []

            creation_time = datetime.now().isoformat()

            if item:
                try:
                    type = item["type"]
                    tags = item["tags"]
                    props = item["data"]
                    knowledge_base = item["knowledgeBase"]
                    name = item["name"]

                    print(
                        f"Processing document chunks for {name} of type {type} with tags {tags} and data {props} and knowledge base {knowledge_base}"
                    )

                    file_extension = get_file_extension(name, type)

                    print(
                        f"Using file extension of {file_extension} based on mime type priority (if present and guessable)"
                    )

                    # Extract text from the file in S3
                    file_content = get_file_from_s3(bucket, key)
                    dochash = hashlib.sha256(file_content).hexdigest()

                    dochash_resposne = hash_files_table.get_item(Key={"id": dochash})

                    # Create a pointer from the user's key to the actual
                    # location of the document by hash
                    [file_text_content_bucket_name, text_content_key] = (
                        get_text_hash_content_location(bucket, dochash)
                    )

                    user_key_to_hash_entry = {
                        "id": key,
                        "hash": dochash,
                        "textLocationBucket": file_text_content_bucket_name,
                        "textLocationKey": text_content_key,
                        "createdAt": creation_time,
                    }
                    hash_files_table.put_item(Item=user_key_to_hash_entry)

                    if not user:
                        user = key.split("/")[0]
                    permissions_update = {
                        "dataSources": [text_content_key],
                        "emailList": [user],
                        "permissionLevel": "write",
                        "policy": "",
                        "principalType": "user",
                        "objectType": "fileEmbedding",
                    }
                    update_object_permissions(user, permissions_update)

                    text = None
                    # Check if already processed AND not a force reprocess request AND embedding was successful
                    if dochash_resposne.get("Item") is not None and not force_reprocess and is_embedding_successful(key):
                        print(f"✅ Document {key} already processed and embedding completed successfully - skipping")
                        text_bucket = dochash_resposne.get("Item").get(
                            "textLocationBucket"
                        )
                        text_key = dochash_resposne.get("Item").get("textLocationKey")
                        print(f"Getting existing text from {text_bucket}/{text_key}")
                        text = json.loads(get_file_from_s3(text_bucket, text_key))
                        print(f"Got existing text from {text_bucket}/{text_key}")
                        total_tokens = text.get("totalTokens", 0)
                        total_items = text.get("totalItems", 0)
                        location_properties = text.get("locationProperties", [])
                        tags = text.get("tags", [])
                        props = text.get("props", [])
                    else:
                        # Process document if: not processed before, OR force reprocess, OR embedding incomplete/failed
                        if force_reprocess:
                            print(f"🔄 Force reprocessing document {key}")
                        elif dochash_resposne.get("Item") is not None:
                            print(f"⚠️ Document {key} processed but embedding incomplete/failed - reprocessing")
                        else:
                            print(f"🆕 New document {key} - processing")
                        text = asyncio.run(
                            extract_text_from_file(file_extension, file_content, user, account_data)
                        )
                        print(f"Extracted text from {key}")
                        total_tokens = sum(d.get("tokens", 0) for d in text)
                        total_items = len(text)

                        if total_items > 0:
                            location_properties = list(
                                text[0].get("location", {}).keys()
                            )

                        text = {
                            "name": name,
                            "totalItems": total_items,
                            "locationProperties": location_properties,
                            "content": text,
                            "createdAt": creation_time,
                            "totalTokens": total_tokens,
                            "tags": tags,
                            "props": props,
                        }
                        if text is not None:
                            print(
                                f"Uploading text to {file_text_content_bucket_name}/{text_content_key}"
                            )
                            # Put the text into a file and upload to S3 bucket
                            # use a random uuid for the key
                            s3.put_object(
                                Bucket=file_text_content_bucket_name,
                                Key=text_content_key,
                                Body=json.dumps(text),
                            )
                            print(
                                f"Uploaded text to {file_text_content_bucket_name}/{text_content_key}"
                            )

                            hash_file_data = {
                                "id": dochash,
                                "originalCreator": user,
                                "textLocationBucket": file_text_content_bucket_name,
                                "textLocationKey": text_content_key,
                                "createdAt": creation_time,
                            }
                            hash_files_table.put_item(Item=hash_file_data)
                            print(f"Updated hash files entry for {dochash}")

                            files_table.update_item(
                                Key={"id": key},
                                UpdateExpression="SET totalTokens = :tokenVal, totalItems = :itemVal, dochash = :hashVal",
                                ExpressionAttributeValues={
                                    ":tokenVal": total_tokens,
                                    ":itemVal": total_items,
                                    ":hashVal": dochash,
                                },
                            )
                            print(
                                f"Uploaded user files entry with token and item "
                                f"count for {key}: {total_tokens} / {total_items}"
                            )

                            print(f"RAG enabled: {rag_enabled}")

                            if not rag_enabled:
                                print( f"RAG chunking is disabled, skipping chunk queue...")
                            else:
                                chunk_queue_url = os.environ["RAG_CHUNK_DOCUMENT_QUEUE_URL"]
                                print("Sending message to chunking queue")
                                try:
                                    record = {
                                        "force_reprocess": force_reprocess,
                                        "s3": {
                                            "bucket": { "name": file_text_content_bucket_name },
                                            "object": { "key": text_content_key },
                                            "metadata": { "object_key": key }
                                        }
                                    }
                                    message_body = json.dumps(record)
                                    sqs.send_message(
                                        QueueUrl=chunk_queue_url,
                                        MessageBody=message_body,
                                    )
                                    print(f"Message sent to queue: {message_body}")
                                except Exception as e:
                                    print(
                                        f"Error sending message to chunking queue: {str(e)}"
                                    )

                except Exception as e:
                    print(f"Error processing document: {str(e)}")

            # If text extraction was successful, delete the message from the queue
            if text is not None:

                [_, text_content_key] = get_text_content_location(bucket, key)

                text_metadata = {
                    "name": name,
                    "totalItems": total_items,
                    "locationProperties": location_properties,
                    "contentKey": text_content_key,
                    "createdAt": creation_time,
                    "totalTokens": total_tokens,
                    "tags": tags,
                    "props": props,
                }

                [file_text_metadata_bucket_name, text_metadata_key] = (
                    get_text_metadata_location(bucket, key)
                )

                s3.put_object(
                    Bucket=file_text_metadata_bucket_name,
                    Key=text_metadata_key,
                    Body=json.dumps(text_metadata),
                )
                print(
                    f"Uploaded metadata to {file_text_metadata_bucket_name}/{text_metadata_key}"
                )

        except Exception as e:
            print(f"Error processing SQS message: {str(e)}")

    return {"statusCode": 200, "body": json.dumps("SQS Text Extraction Complete!")}


def update_embedding_status(original_creator, object_id, total_chunks, status):
    try:
        progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
        print(
            f"Updating chunk count status for embedding {progress_table}/{object_id} "
            f"Total Chunks: {total_chunks} {status}"
        )

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)

        # Check if there's existing progress data (for selective reprocessing)
        try:
            response = table.get_item(Key={"object_id": object_id})
            existing_item = response.get("Item")
            
            if existing_item and existing_item.get("data", {}).get("childChunks"):
                # Preserve existing chunk statuses that are completed
                existing_chunks = existing_item["data"]["childChunks"]
                new_chunks = {}
                
                for i in range(total_chunks):
                    chunk_id = str(i + 1)
                    if chunk_id in existing_chunks and existing_chunks[chunk_id].get("status") == "completed":
                        # Preserve completed chunks
                        new_chunks[chunk_id] = existing_chunks[chunk_id]
                        print(f"Preserving completed status for chunk {chunk_id}")
                    else:
                        # Reset non-completed chunks to starting
                        new_chunks[chunk_id] = {"status": status}
                
                child_chunks = new_chunks
                print(f"Preserved {len([c for c in new_chunks.values() if c.get('status') == 'completed'])} completed chunks")
            else:
                # No existing data, create fresh
                child_chunks = {str(i + 1): {"status": status} for i in range(total_chunks)}
        except Exception as e:
            print(f"Error checking existing progress: {e}")
            # Fallback to creating fresh
            child_chunks = {str(i + 1): {"status": status} for i in range(total_chunks)}

        table.put_item(
            Item={
                "object_id": object_id,
                "parentChunkStatus": status,
                "timestamp": datetime.now().isoformat(),
                "originalCreator": original_creator,
                "terminated": False,
                "totalChunks": total_chunks,
                "data": {
                    "childChunks": child_chunks
                },
            }
        )
        print(
            f"Updated {total_chunks} nested childChunks for {object_id} in Embeddings Progress Table"
        )

    except Exception as e:
        print("Failed to create or update item in DynamoDB table.")
        print(e)


def get_original_creator(key):
    original_creator = "unknown"
    try:
        dynamodb = boto3.resource("dynamodb")
        # Get the original creator of the file
        # by extracting the hash from the key
        # and looking up the original creator in the hash_files table
        hash = key
        parts = key.split("/")
        if len(parts) == 2:
            filename = parts[1]
            hash = filename.split(".")[0]
        hash_files = dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"])
        response = hash_files.get_item(Key={"id": hash})
        item = response.get("Item", None)
        if item:
            print(f"Found hash entry for {key}: {item}")
            original_creator = item.get("originalCreator", "unknown")
            print(f"Original uploader: {original_creator}")
        else:
            print(f"Hash entry not found for {key}")
    except Exception as e:
        print(
            f"Error getting hash entry for {key} to determine original_creator: {str(e)}"
        )

    return original_creator


def is_embedding_successful(document_key):
    """
    Check if embedding process was successful for a document.
    
    Args:
        document_key: The document key to check embedding status for
        
    Returns:
        bool: True if embedding completed successfully, False otherwise
    """
    try:
        from pycommon.api.data_sources import translate_user_data_sources_to_hash_data_sources
        
        # Translate document key to global hash
        translated_sources = translate_user_data_sources_to_hash_data_sources([{"id": document_key, "type": "document"}])
        if not translated_sources or not translated_sources[0].get("id"):
            print(f"[EMBEDDING_CHECK] Could not get global hash for {document_key}")
            return False
            
        global_id = translated_sources[0]["id"]
        
        # Check embedding progress table
        progress_table = os.environ.get("EMBEDDING_PROGRESS_TABLE")
        if not progress_table:
            print(f"[EMBEDDING_CHECK] EMBEDDING_PROGRESS_TABLE not configured")
            return False
            
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)
        
        response = table.get_item(Key={"object_id": global_id})
        item = response.get("Item")
        
        if not item:
            print(f"[EMBEDDING_CHECK] No embedding progress found for {document_key}")
            return False
        
        # Check if terminated
        if item.get("terminated", False):
            print(f"[EMBEDDING_CHECK] Embedding process terminated for {document_key}")
            return False
        
        # Check parent chunk status
        parent_status = item.get("parentChunkStatus")
        if parent_status == "completed":
            print(f"[EMBEDDING_CHECK] ✅ Embedding completed successfully for {document_key}")
            return True
        else:
            print(f"[EMBEDDING_CHECK] Embedding not completed for {document_key} (status: {parent_status})")
            return False
            
    except Exception as e:
        print(f"[EMBEDDING_CHECK] Error checking embedding status for {document_key}: {e}")
        # On error, return False to trigger reprocessing for safety
        return False



def chunk_document_for_rag(event, context):
    print(f"Received event: {event}")

    for record in event["Records"]:
        try:
            print(f"Processing message: {record}")
            # Assuming the message body is a JSON string, parse it
            message_data = json.loads(record["body"])
            print(f"Message body: {message_data}")
            
            # Check if this is a force reprocessing request
            force_reprocess = message_data.get("force_reprocess", False)
            print(f"Force reprocess flag: {force_reprocess}")
            
            s3_info = message_data["s3"]

            # Extract object_key from metadata if present
            object_key = s3_info.get("metadata", {}).get("object_key")
            print(f"Object key from metadata: {object_key}")

            # Get the bucket and object key from the event
            print(f"Getting raw text from {s3_info['object']['key']}")
            bucket = s3_info["bucket"]["name"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            print(f"Bucket / Key {bucket} / {key}")

            # Figure out who uploaded this file, even though it's a shared
            # global entry
            original_creator = get_original_creator(key)

            # Use original chunking method - no selective processing for now to avoid complexity
            chunks_created = chunk_s3_file_content(bucket, key, object_key, force_reprocess)
            print(f"[CHUNKING] Created {chunks_created} chunk files for {key}")
            
            if chunks_created == 0:
                print(f"No chunks were created for {key}")
                continue

            # Use chunk FILES count, not individual chunks
            # The embedding service processes chunk files, not individual chunks
            update_embedding_status(original_creator, key, chunks_created, "starting")

        except Exception as e:
            print(f"Error processing SQS message: {str(e)}")

    return {"statusCode": 200, "body": json.dumps("SQS Text Extraction Complete!")}


############ Local Test Extraction ############


def test_extract_text_locally(file_path):
    """
    Test function to extract text from a local file for development/testing purposes.

    Args:
        file_path (str): Path to the local file to process

    Returns:
        list: List of extracted text chunks
    """

    if not os.path.exists(file_path):
        print(f"Error: File {file_path} does not exist")
        return []

    try:
        # Read the file content as bytes
        with open(file_path, "rb") as f:
            file_content = f.read()

        # Use the filename as the key
        filename = os.path.basename(file_path)

        # Hardcode user to empty string as requested
        current_user = "your_email"

        print(f"Testing text extraction for file: {filename}")
        print(f"File size: {len(file_content)} bytes")

        # Call the async extract_text_from_file function
        result = asyncio.run(
            extract_text_from_file(filename, file_content, current_user, 
                                   {"account": None,
                                    "rate_limit": None,
                                    "access_token": "amp-",
                                    }
)
        )

        print(f"Extraction completed. Found {len(result)} text chunks.")

        # Print a summary of the results
        if result:
            total_chars = sum(len(chunk.get("content", "")) for chunk in result)
            print(f"Total characters extracted: {total_chars}")
            print("Chunks: ", result)

        return result

    except Exception as e:
        print(f"Error processing file {file_path}: {str(e)}")
        import traceback

        traceback.print_exc()
        return []


if __name__ == "__main__":
    # Test with a local file
    filename = "Test.pdf"  # Change this to your test file name

    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)

    print(f"Looking for file at: {file_path}")
    result = test_extract_text_locally(file_path)
    print(f"Extracted {len(result)} chunks")
