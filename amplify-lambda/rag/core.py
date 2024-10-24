
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import base64
import hashlib
import os
import mimetypes
import boto3
import json
import chardet
import urllib.parse
from datetime import datetime
import re
from rag.handlers.commaseparatedvalues import CSVHandler
from rag.handlers.excel import ExcelHandler
from rag.handlers.pdf import PDFHandler
from rag.handlers.powerpoint import PPTXHandler
from rag.handlers.word import DOCXHandler
from rag.handlers.text import TextHandler
from rag.util import get_text_content_location, get_text_metadata_location, get_text_hash_content_location
import traceback
from cryptography.fernet import Fernet

s3 = boto3.client('s3')
sqs = boto3.client('sqs')


def is_likely_text(file_content):
    # Use chardet to detect the encoding of the file_content
    result = chardet.detect(file_content)
    confidence = result['confidence']  # How confident chardet is about its detection
    encoding = result['encoding']
    is_text = result['encoding'] is not None and confidence > 0.7  # You can adjust the confidence threshold

    return is_text, encoding


def decode_text(file_content, encoding):
    # Decode the file content using the detected encoding
    try:
        text = file_content.decode(encoding)
        return text
    except UnicodeDecodeError as e:
        # You can handle decoding errors differently if necessary
        raise ValueError(f"Decoding failed for encoding {encoding}: {e}")


def get_handler_and_split_params(key):
    if key.endswith('.pdf'):
        return PDFHandler(), {}
    elif key.endswith('.docx'):
        return DOCXHandler(), {}
    elif key.endswith('.pptx'):
        return PPTXHandler(), {}
    elif key.endswith('.xls') or key.endswith('.xlsx'):
        return ExcelHandler(), {'min_chunk_size': 512}
    elif key.endswith('.csv'):
        return CSVHandler(), {'min_chunk_size': 512}
    elif key.endswith('.html'):
        return None  # HTMLHandler(), {}
    else:
        return TextHandler(), {}


def get_text_extraction_handler(key):
    if key.endswith('.pdf'):
        return PDFHandler()
    elif key.endswith('.docx'):
        return DOCXHandler()
    elif key.endswith('.pptx'):
        return PPTXHandler()
    elif key.endswith('.xls') or key.endswith('.xlsx'):
        return ExcelHandler()
    elif key.endswith('.csv'):
        return CSVHandler()
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


# Extract text from file and return an array of chunks
def extract_text_from_file(key, file_content):
    # Get the appropriate handler and split parameters for the file type
    handler = get_text_extraction_handler(key)

    if handler:
        try:
            return handler.extract_text(file_content, key)
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
                return TextHandler().extract_text(text_content, key)
            except Exception as e:
                print(f"Error extracting text from {key}: {str(e)}")
                # Return an empty list to indicate that no chunks could be extracted
                return []


def split_text(sent_tokenize, content):
    if content.get('canSplit'):
        text = re.sub(r'\s+', ' ', content['content'].strip())
        # Sentences will not need whitespace normalization.
        sentences = sent_tokenize(text)

        return [{**content, 'content': sentence, 'index': index} for index, sentence in enumerate(sentences)]
    else:
        return [content]


def save_chunks(chunks_bucket, key, split_count, chunks):
    print(f"Saving chunks {len(chunks)} to {chunks_bucket}/{key}-{split_count}.chunks.json")
    chunks_key = f"{key}-{split_count}.chunks.json"
    s3.put_object(Bucket=chunks_bucket,
                  Key=chunks_key,
                  Body=json.dumps({'chunks': chunks, 'src': key}))
    print(f"Uploaded chunks to {chunks_bucket}/{chunks_key}")


def chunk_content(key, text_content, split_params):
    import nltk
    # nltk.download('punkt')
    from nltk.tokenize import sent_tokenize

    nltk.data.path.append("/tmp")
    nltk.download("punkt", download_dir="/tmp")
    # Normalize whitespace once at the start.

    chunks = []
    current_chunk = []
    current_chunk_size = 0
    char_index = 0
    content_index = 0
    min_chunk_size = split_params.get('min_chunk_size', 512)

    total_chunks = 0
    max_chunks = int(os.environ.get('MAX_CHUNKS', '1000'))

    parts = [split_text(sent_tokenize, item) for item in text_content['content']]
    flattened_list = [item for sublist in parts for item in sublist]

    locations = []
    indexes = []

    chunks_bucket = os.environ['S3_RAG_CHUNKS_BUCKET_NAME']
    split_increment = 10
    split_count = 0
    index = 0

    total_chunks = 0  # Initialize total_chunks
  
    for content_part in flattened_list:
        sentence = content_part['content']
        location = content_part['location']
        sentence_length = len(sentence)

        if total_chunks >= max_chunks:
            print(f"Reached maximum chunks {max_chunks} for {key}")
            break

        # Check if adding this sentence would exceed the chunk size.
        if current_chunk and (current_chunk_size + sentence_length + 1) >= min_chunk_size:
            # Join the current chunk with space and create the chunk object.
            chunk_text = ' '.join(current_chunk)

            chunks.append({'content': chunk_text,
                           'locations': locations,
                           'indexes': indexes,
                           'char_index': char_index})

            # Reset for the next chunk
            locations = []
            indexes = []
            char_index += len(chunk_text) + 1  # Include the space that joins with the next chunk.
            current_chunk = [sentence]  # Start the new chunk with the current sentence.
            current_chunk_size = sentence_length

            total_chunks += 1  # Increment the count after forming a chunk

            if len(chunks) == split_increment:
                split_count += 1
                save_chunks(chunks_bucket, key, split_count, chunks)
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
        chunk_text = ' '.join(current_chunk)
        chunks.append({'content': chunk_text,
                       'locations': locations,
                       'indexes': indexes,
                       'char_index': char_index})
        total_chunks += 1  # Increment the count for the last chunk

    if chunks:  # If there are unfinished chunks, save them
        split_count += 1
        save_chunks(chunks_bucket, key, split_count, chunks)
    
    print(f"In Chunk Content Function")
    print(f"Split Count: {split_count}, Split Increment: {split_increment}, Total Chunks: {total_chunks}")
    return split_count


def chunk_s3_file_content(bucket, key):
    try:
        # Download the file from S3
        print(f"Fetching text from {bucket}/{key}")
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        data = s3_object["Body"].read()
        print(f"Fetched text from {bucket}/{key}")

        file_content = json.loads(data)
        print(f"Loaded json from {bucket}/{key}")

        # Extract text from the file in S3
        chunks = chunk_content(key, file_content, {})
        print(f"Chunk S3 File Content Function: Chunked content for {key} into {chunks} chunks")

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
        with open(os.path.join(directory_path, filename), 'rb') as f:
            file_content = f.read()

        # Use the previously defined extract_text_from_file function to get chunks
        # Notice that this function might need to be adapted depending on the handlers implementations
        document_structure = extract_text_from_file(filename, file_content)
        data = {
            'name': filename,
            'content': document_structure,
        }

        # Save the chunks to a file
        with open(os.path.join(directory_path, filename + '.content.json'), 'w') as f:
            json.dump(data, f, indent=2)


def extract_text_from_s3_file(bucket, key, file_extension):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = s3_object["Body"].read()

        return extract_text_from_file(file_extension, file_content)

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


def queue_document_for_rag(event, context):
    queue_url = os.environ['rag_process_document_queue_url']

    print(f"Received event: {event}")
    print(f"{event}")
    for record in event['Records']:
        # Send the S3 object data as a message to the SQS queue
        message_body = json.dumps(record)
        print(f"Sending message to queue: {message_body}")
        sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
        print(f"Message sent to queue: {message_body}")

    return {'statusCode': 200, 'body': json.dumps('Successfully sent to SQS')}


def update_object_permissions(current_user, data):
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']

    try:
        data_sources = data['dataSources']
        email_list = data['emailList']
        provided_permission_level = data['permissionLevel']  # Permission level provided for other users
        policy = data['policy']  # No need to use get() since policy is always present
        principal_type = data.get('principalType')
        object_type = data.get('objectType')

        print(f"Updating permission on {data_sources} for {email_list} with {provided_permission_level} and {policy}")

        # Get the DynamoDB table
        table = dynamodb.Table(table_name)

        for object_id in data_sources:
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('object_id').eq(object_id)
            )
            items = query_response.get('Items')

            # If there are no permissions, create the initial item with the current_user as the owner
            if not items:
                table.put_item(Item={
                    'object_id': object_id,
                    'principal_id': current_user,
                    'principal_type': principal_type,
                    'object_type': object_type,
                    'permission_level': 'write',  # The current_user becomes the owner 
                    'policy': policy
                }) 
                print(f"Created initial item for {object_id} with {current_user} as owner")

            else:
                # If current_user is the owner or has write permission, proceed with updates
                for principal_id in email_list:
                    # Create or update the permission level for each principal_id
                    principal_key = {
                        'object_id': object_id,
                        'principal_id': principal_id
                    }
                    # Use the provided permission level for other users
                    update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
                    expression_attribute_values = {
                        ':principal_type': principal_type,
                        ':object_type': object_type,
                        ':permission_level': provided_permission_level,  # Use the provided permission level
                        ':policy': policy
                    }
                    table.update_item(
                        Key=principal_key,
                        UpdateExpression=update_expression,
                        ExpressionAttributeValues=expression_attribute_values
                    )
                    print(f"Updated item for {object_id} with {principal_id} to {provided_permission_level}")

    except Exception as e:
        print(f"Failed to update permissions: {str(e)}")
        return False

    print(f"Updated permissions for {data_sources} for {email_list} with {provided_permission_level} and {policy}")
    return True


def decrypt_account_data(encrypted_data_b64):
    ssm_client = boto3.client('ssm')
    parameter_name = os.getenv('ENCRYPTION_PARAMETER')
    print("Enter decrypt account data")
    try:
        # Fetch the parameter securely, which holds the encryption key
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        # The key needs to be a URL-safe base64-encoded 32-byte key
        key = response['Parameter']['Value'].encode()
        # Ensure the key is in the correct format for Fernet
        fernet = Fernet(key)

        encrypted_data = base64.b64decode(encrypted_data_b64)

        # Decrypt the data
        decrypted_str = fernet.decrypt(encrypted_data).decode('utf-8')

        decrypted_data = json.loads(decrypted_str)

        print("Decrypted value:", decrypted_data)
        return {
            'success': True,
            'decrypted_data': decrypted_data
        }

    except Exception as e:
        print(f"Error during parameter retrieval or encryption {parameter_name}: {str(e)}")
        return {
            'success': False,
            'error': f"Error {str(e)}"
        }
    

def process_document_for_rag(event, context):
    print(f"Received event: {event}")
    s3 = boto3.client('s3')
    queue_url = os.environ['rag_process_document_queue_url']

    dynamodb = boto3.resource('dynamodb')
    files_table = dynamodb.Table(os.environ['FILES_DYNAMO_TABLE'])
    hash_files_table = dynamodb.Table(os.environ['HASH_FILES_DYNAMO_TABLE'])

    for record in event['Records']:
        try:
            print(f"Processing message: {record}")
            # Assuming the message body is a JSON string, parse it
            s3_info = json.loads(record['body'])
            print(f"Message body: {s3_info}")
            s3_info = s3_info["s3"]

            # Get the bucket and object key from the event
            print(f"Getting text from {s3_info['object']['key']}")
            bucket = s3_info['bucket']['name']
            key = s3_info['object']['key']
            key = urllib.parse.unquote(key)

            print(f"Bucket / Key {bucket} / {key}")

            #get decrypted account data 
            account = None
            apiKey = None
            user = None
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                encrypted_metadata_b64 = response['Metadata']['encrypted_metadata'] 
                print("Encrypted Metadata:", encrypted_metadata_b64)
                
                # Decrypt the metadata
                decryption_result = decrypt_account_data(encrypted_metadata_b64)
                if decryption_result['success']:
                    decrypted_data = decryption_result['decrypted_data']
                    print("Decrypted Metadata:", decrypted_data)
                    account = decrypted_data["account"]
                    apiKey = decrypted_data['api_key']
                    user = decrypted_data['user']
                else:
                    print("Failed to decrypt metadata:", decryption_result['error'])

            except Exception as e:
                print(f"Error fetching metadata for {key}: {str(e)}")

            response = files_table.get_item(
                Key={
                    'id': key
                }
            )

            # The rest is the same as above
            item = response.get('Item', None)

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
                    type = item['type']
                    tags = item['tags']
                    props = item['data']
                    knowledge_base = item['knowledgeBase']
                    name = item['name']

                    print(
                        f"Processing document chunks for {name} of type {type} with tags {tags} and data {props} and knowledge base {knowledge_base}")

                    file_extension = get_file_extension(name, type)

                    print(
                        f"Using file extension of {file_extension} based on mime type priority (if present and guessable)")

                    # Extract text from the file in S3
                    file_content = get_file_from_s3(bucket, key)
                    dochash = hashlib.sha256(file_content).hexdigest()

                    dochash_resposne = hash_files_table.get_item(
                        Key={
                            'id': dochash
                        }
                    )

                    # Create a pointer from the user's key to the actual
                    # location of the document by hash
                    [file_text_content_bucket_name, text_content_key] = \
                        get_text_hash_content_location(bucket, dochash)

                    user_key_to_hash_entry = {
                        'id': key,
                        'hash': dochash,
                        'textLocationBucket': file_text_content_bucket_name,
                        'textLocationKey': text_content_key,
                        'createdAt': creation_time,
                    }
                    hash_files_table.put_item(Item=user_key_to_hash_entry)

                    if not user: user = key.split('/')[0]
                    permissions_update = {
                        'dataSources': [text_content_key],
                        'emailList': [user],
                        'permissionLevel': 'write',
                        'policy': '',
                        'principalType': 'user',
                        'objectType': 'fileEmbedding'
                    }
                    update_object_permissions(user, permissions_update)
                    # data_sources = data['dataSources']
                    # email_list = data['emailList']
                    # provided_permission_level = data['permissionLevel']  # Permission level provided for other users
                    # policy = data['policy']  # No need to use get() since policy is always present
                    # principal_type = data.get('principalType')
                    # object_type = data.get('objectType')

                    text = None
                    if dochash_resposne.get('Item') is not None:
                        print(f"Document {key} already processed")
                        text_bucket = dochash_resposne.get('Item').get('textLocationBucket')
                        text_key = dochash_resposne.get('Item').get('textLocationKey')
                        print(f"Getting text from {text_bucket}/{text_key}")
                        text = json.loads(get_file_from_s3(text_bucket, text_key))
                        print(f"Got text from {text_bucket}/{text_key}")
                        total_tokens = text.get('totalTokens', 0)
                        total_items = text.get('totalItems', 0)
                        location_properties = text.get('locationProperties', [])
                        tags = text.get('tags', [])
                        props = text.get('props', [])
                    else:
                        text = extract_text_from_file(file_extension, file_content)
                        print(f"Extracted text from {key}")
                        total_tokens = sum(d.get('tokens', 0) for d in text)
                        total_items = len(text)

                        if total_items > 0:
                            location_properties = list(text[0].get('location', {}).keys())

                        text = {
                            'name': name,
                            'totalItems': total_items,
                            'locationProperties': location_properties,
                            'content': text,
                            'createdAt': creation_time,
                            'totalTokens': total_tokens,
                            'tags': tags,
                            'props': props,
                        }
                        if text is not None:
                            print(f"Uploading text to {file_text_content_bucket_name}/{text_content_key}")
                            # Put the text into a file and upload to S3 bucket
                            # use a random uuid for the key
                            s3.put_object(Bucket=file_text_content_bucket_name,
                                          Key=text_content_key,
                                          Body=json.dumps(text))
                            print(f"Uploaded text to {file_text_content_bucket_name}/{text_content_key}")

                            hash_file_data = {
                                'id': dochash, 
                                'originalCreator': user,
                                'textLocationBucket': file_text_content_bucket_name,
                                'textLocationKey': text_content_key,
                                'createdAt': creation_time,
                                'account' : account, 
                                'apiKey' : apiKey
                            }
                            hash_files_table.put_item(Item=hash_file_data)
                            print(f"Updated hash files entry for {dochash}")

                            files_table.update_item(
                                Key={
                                    'id': key
                                },
                                UpdateExpression='SET totalTokens = :tokenVal, totalItems = :itemVal, dochash = :hashVal',
                                ExpressionAttributeValues={
                                    ':tokenVal': total_tokens,
                                    ':itemVal': total_items,
                                    ':hashVal': dochash
                                }
                            )
                            print(
                                f"Uploaded user files entry with token and item "
                                f"count for {key}: {total_tokens} / {total_items}")

                except Exception as e:
                    print(f"Error processing document: {str(e)}")

            # If text extraction was successful, delete the message from the queue
            if text is not None:

                # [_, text_content_key] = get_text_content_location(bucket, dochash)
                [_, text_content_key] = get_text_content_location(bucket, key)

                text_metadata = {
                    'name': name,
                    'totalItems': total_items,
                    'locationProperties': location_properties,
                    'contentKey': text_content_key,
                    'createdAt': creation_time,
                    'totalTokens': total_tokens,
                    'tags': tags,
                    'props': props,
                }

                [file_text_metadata_bucket_name, text_metadata_key] = \
                    get_text_metadata_location(bucket, key)

                s3.put_object(Bucket=file_text_metadata_bucket_name,
                              Key=text_metadata_key,
                              Body=json.dumps(text_metadata))
                print(f"Uploaded metadata to {file_text_metadata_bucket_name}/{text_metadata_key}")

                receipt_handle = record['receiptHandle']
                print(f"Deleting message {receipt_handle} from queue")

                # Delete received message from queue
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle
                )
                print(f"Deleted message {record['messageId']} from queue")
            else:
                print(f"Failed to extract text from {key}")

        except Exception as e:
            print(f"Error processing SQS message: {str(e)}")

    return {
        'statusCode': 200,
        'body': json.dumps('SQS Text Extraction Complete!')
    }


def queue_document_for_rag_chunking(event, context):
    queue_url = os.environ['rag_chunk_document_queue_url']

    print(f"Received chunk event: {event}")
    print(f"{event}")
    for record in event['Records']:
        # Send the S3 object data as a message to the SQS queue
        message_body = json.dumps(record)
        print(f"Sending message to queue: {message_body}")
        sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
        print(f"Message sent to queue: {message_body}")

    return {'statusCode': 200, 'body': json.dumps('Successfully sent to SQS')}


def update_embedding_status(original_creator, object_id, chunk_index, total_chunks, status):
    try:
        progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
        print(f"Updating chunk count status for embedding {progress_table}/{object_id} "
              f"{chunk_index}/{total_chunks} {status}")
        
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(progress_table)
        
        table.put_item(
            Item={
                'object_id': object_id,
                'parentChunkStatus': status,
                'timestamp': datetime.now().isoformat(),
                'originalCreator': original_creator,
                'terminated': False,
                'totalChunks': total_chunks,
                'data': {
                    'childChunks': {
                        str(i+1): {'status': status} for i in range(total_chunks)
                    }   
                }
            }
        )
        print (f"Created {total_chunks} nested childCunks for {object_id} in Embeddings Progress Table")

    except Exception as e:
        print("Failed to create or update item in DynamoDB table.")
        print(e)


def get_original_creator(key):
    original_creator = 'unknown'
    try:
        dynamodb = boto3.resource('dynamodb')
        # Get the original creator of the file
        # by extracting the hash from the key
        # and looking up the original creator in the hash_files table
        hash = key
        parts = key.split('/')
        if len(parts) == 2:
            filename = parts[1]
            hash = filename.split('.')[0]
        hash_files = dynamodb.Table(os.environ['HASH_FILES_DYNAMO_TABLE'])
        response = hash_files.get_item(
            Key={
                'id': hash
            }
        )
        item = response.get('Item', None)
        if item:
            print(f"Found hash entry for {key}: {item}")
            original_creator = item.get('originalCreator', 'unknown')
            print(f"Original uploader: {original_creator}")
        else:
            print(f"Hash entry not found for {key}")
    except Exception as e:
        print(f"Error getting hash entry for {key} to determine original_creator: {str(e)}")

    return original_creator


def chunk_document_for_rag(event, context):
    print(f"Received event: {event}")
    queue_url = os.environ['rag_chunk_document_queue_url']

    dynamodb = boto3.resource('dynamodb')
    files_table = dynamodb.Table(os.environ['FILES_DYNAMO_TABLE'])


    for record in event['Records']:
        try:
            print(f"Processing message: {record}")
            # Assuming the message body is a JSON string, parse it
            s3_info = json.loads(record['body'])
            print(f"Message body: {s3_info}")
            s3_info = s3_info["s3"]

            # Get the bucket and object key from the event
            print(f"Getting raw text from {s3_info['object']['key']}")
            bucket = s3_info['bucket']['name']
            key = s3_info['object']['key']
            key = urllib.parse.unquote(key)

            if key.endswith('.metadata.json'):
                print(f"Skipping metadata file {key}")
                continue

            print(f"Bucket / Key {bucket} / {key}")

            # Figure out who uploaded this file, even though it's a shared
            # global entry
            original_creator = get_original_creator(key)

            chunks = chunk_s3_file_content(bucket, key)

            update_embedding_status(original_creator, key, 0, chunks, "starting")

            receipt_handle = record['receiptHandle']
            print(f"Deleting message {receipt_handle} from queue")

            # Delete received message from queue
            sqs.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle
            )
            print(f"Deleted message {record['messageId']} from queue")


        except Exception as e:
            print(f"Error processing SQS message: {str(e)}")

    return {
        'statusCode': 200,
        'body': json.dumps('SQS Text Extraction Complete!')
    }
