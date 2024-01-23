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
from rag.util import get_text_content_location, get_text_metadata_location

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
        return None #HTMLHandler(), {}
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

def chunk_content(text_content, split_params):
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

    parts = [split_text(sent_tokenize, item) for item in text_content['content']]
    flattened_list = [item for sublist in parts for item in sublist]

    locations = []
    indexes = []

    for content_part in flattened_list:
        sentence = content_part['content']
        location = content_part['location']
        index = content_part['index']
        sentence_length = len(sentence)

        # Check if adding this sentence would exceed the chunk size.
        if current_chunk and (current_chunk_size + sentence_length + 1) >= min_chunk_size:
            # Join the current chunk with space and create the chunk object.
            chunk_text = ' '.join(current_chunk)

            chunks.append({'content': chunk_text,
                           'locations': locations,
                           'indexes': indexes,
                           'char_index': char_index})

            locations = []
            indexes = []
            # Update char_index and reset current_chunk.
            char_index += len(chunk_text) + 1  # Include the space that joins with the next chunk.
            current_chunk = [sentence]  # Start the new chunk with the current sentence.
            current_chunk_size = sentence_length
            content_index += 1  # Increment the content index.
        else:
            locations.append(location)
            indexes.append(index)
            # If this is the first sentence, don't add a space at the start.
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

    return chunks


def chunk_s3_file_content(bucket, key):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = json.loads(s3_object["Body"].read())

        # Extract text from the file in S3
        chunks = chunk_content(file_content, {})

        return chunks

    except Exception as e:
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


def process_document_for_rag(event, context):
    print(f"Received event: {event}")
    queue_url = os.environ['rag_process_document_queue_url']

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
            print(f"Getting text from {s3_info['object']['key']}")
            bucket = s3_info['bucket']['name']
            key = s3_info['object']['key']
            key = urllib.parse.unquote(key)

            print(f"Bucket / Key {bucket} / {key}")

            response = files_table.get_item(
                Key={
                    'id': key
                }
            )

            # The rest is the same as above
            item = response.get('Item', None)

            total_items = 0
            text = None
            location_properties = []

            if item:
                try:
                    type = item['type']
                    tags = item['tags']
                    props = item['data']
                    knowledge_base = item['knowledgeBase']
                    name = item['name']

                    print(f"Processing document chunks for {name} of type {type} with tags {tags} and data {props} and knowledge base {knowledge_base}")

                    file_extension = get_file_extension(name, type)

                    print(f"Using file extension of {file_extension} based on mime type priority (if present and guessable)")

                    # Extract text from the file in S3
                    text = extract_text_from_s3_file(bucket, key, file_extension)
                    total_tokens = sum(d.get('tokens', 0) for d in text)
                    total_items = len(text)

                    if total_items > 0:
                        location_properties = list(text[0].get('location', {}).keys())

                    text = {
                        'name': name,
                        'totalItems': total_items,
                        'locationProperties': location_properties,
                        'content': text,
                        'totalTokens': total_tokens,
                        'tags': tags,
                        'props': props,
                    }

                    print(f"Extracted text from {key}")
                except Exception as e:
                    print(f"Error processing document: {str(e)}")


            # If text extraction was successful, delete the message from the queue
            if text is not None:

                [file_text_content_bucket_name, text_content_key] = \
                    get_text_content_location(bucket, key)

                print(f"Uploading text to {file_text_content_bucket_name}/{text_content_key}")
                # Put the text into a file and upload to S3 bucket
                # use a random uuid for the key
                s3.put_object(Bucket=file_text_content_bucket_name, Key=text_content_key, Body=json.dumps(text))
                print(f"Uploaded text to {file_text_content_bucket_name}/{text_content_key}")

                text_metadata = {
                    'name': name,
                    'totalItems': total_items,
                    'locationProperties': location_properties,
                    'contentKey': text_content_key,
                    'createdAt': datetime.now().isoformat(),
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

            chunks = chunk_s3_file_content(bucket, key)

            chunks_bucket = os.environ['S3_RAG_CHUNKS_BUCKET_NAME']
            chunks_key = key + '.chunks.json'

            s3.put_object(Bucket=chunks_bucket,
                          Key=chunks_key,
                          Body=json.dumps({'chunks': chunks, 'src': key}))
            print(f"Uploaded chunks to {chunks_bucket}/{chunks_key}")

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
