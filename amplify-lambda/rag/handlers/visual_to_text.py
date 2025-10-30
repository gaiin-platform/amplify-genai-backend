import boto3
import base64
import re
import uuid
import os
import io
import asyncio
from datetime import datetime
import os
from pycommon.api.models import get_default_models

from pycommon.logger import getLogger
logger = getLogger("rag_visual_to_text")

# uncomment for local development
# import sys
# if __name__ == "__main__":
#     # Add the parent directory to Python path for local testing
#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     parent_dir = os.path.dirname(
#         os.path.dirname(current_dir)
#     )  # Go up to amplify-lambda/
#     sys.path.insert(0, parent_dir)

SAVE_IMAGES_LOCALLY = False  # will save extracted images locally for debugging

from images.core import resize_image
from rag.core import update_object_permissions
from pycommon.llm.chat import chat
from pycommon.api.get_endpoint import get_endpoint, EndpointType
from PIL import Image

# Initialize S3 client
s3 = boto3.client("s3")

CHAT_ENDPOINT = get_endpoint(EndpointType.CHAT_ENDPOINT)

def save_visual_to_s3(image_data, current_user):
    """
    Save binary image data to S3 with proper permissions and resizing.

    Args:
        image_data: Binary image data (bytes)
        current_user: User ID for file organization
        visual_type: MIME type for the visual content

    Returns:
        S3 key where the visual was saved
    """
    try:
        # Safety check: Convert BytesIO or file-like objects to raw bytes immediately
        if isinstance(image_data, io.BytesIO):
            image_data.seek(0)
            image_data = image_data.getvalue()
        elif hasattr(image_data, 'read') and hasattr(image_data, 'seek'):
            image_data.seek(0)
            image_data = image_data.read()
        elif hasattr(image_data, 'getvalue'):
            image_data = image_data.getvalue()
        
        # At this point, image_data should be raw bytes
        if not isinstance(image_data, bytes):
            raise ValueError(f"Expected bytes after conversion, got {type(image_data)}")

        # Generate unique key for this visual
        dt_string = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"tmp/{current_user}/{dt_string}/{uuid.uuid4()}.txt"

        # Create PIL Image from raw bytes (simplified since we guarantee bytes above)
        image = Image.open(io.BytesIO(image_data))

        # Resize the image using the same function as process_images_for_chat
        resized_image = resize_image(image)

        # Convert back to base64
        img_buffer = io.BytesIO()
        # Save as PNG to maintain quality
        resized_image.save(img_buffer, format="PNG")
        resized_image_bytes = img_buffer.getvalue()

        # Encode to base64
        encoded_image = base64.b64encode(resized_image_bytes).decode("utf-8")

        bucket_name = os.environ.get("S3_IMAGE_INPUT_BUCKET_NAME")
        if not bucket_name:
            raise Exception("S3_IMAGE_INPUT_BUCKET_NAME environment variable not set")

        # Save the base64 encoded image directly to S3 (like process_images_for_chat)
        s3.put_object(
            Bucket=bucket_name, Key=key, Body=encoded_image, ContentType="text/plain"
        )

        logger.info("Visual content saved to S3: %s", key)

        # Update permissions following the pattern from images/core.py
        permissions_update = {
            "dataSources": [key],
            "emailList": [current_user],
            "permissionLevel": "write",
            "policy": "",
            "principalType": "user",
            "objectType": "datasource",
        }

        # Update permissions
        update_object_permissions(current_user, permissions_update)

        logger.info("Permissions updated for visual: %s", key)

        return key

    except Exception as e:
        logger.error("Error saving visual to S3: %s", str(e))
        raise


NO_SUBSTANCE = "NO_MEANINGFUL_DATA"


async def transcribe_visual_content(key, visual_type="image/png", account_data=None):
    """
    Call LLM vision service to get detailed transcription of visual content.

    Args:
        key: S3 key where visual content is stored
        visual_type: MIME type of the visual content

    Returns:
        Text transcription of the visual content
    """
    try:
        # Get required environment variables

        if not CHAT_ENDPOINT or not account_data:
            logger.error("CHAT_ENDPOINT environment variable or account_data not set")
            raise Exception("CHAT_ENDPOINT environment variable or account_data not set")
        access_token = account_data['access_token']
        
        model = get_default_models(access_token).get("cheapest_model")

        if not model:
            logger.error("No model found")
            raise Exception("No model found")
        
        dataSources = [{"type": visual_type, "id": key}]
        api_accessed = access_token.startswith("amp-")
        
        # Comprehensive system prompt for visual analysis
        system_prompt = """
You are an expert visual content analyzer. Your task is to provide precise, detailed transcriptions of visual content including charts, tables, diagrams, and images. Follow these specific formatting guidelines:

**CRITICAL: FOCUS ON CONTENT, NOT DESIGN**
- Extract DATA and INFORMATION only
- DO NOT describe visual styling, colors, alignment, or design aesthetics
- DO NOT mention: "vibrant", "neatly aligned", "clean design", "modern", color descriptions, positioning details
- FOCUS on: numbers, text, data points, trends, and factual content
- Keep descriptions concise and business-focused

**IMPORTANT: MEANINGLESS CONTENT DETECTION**
If the visual content is purely decorative, artistic, or contains no meaningful information, return exactly:
NO_MEANINGFUL_DATA

**Examples of meaningless content to skip:**
- Abstract backgrounds or decorative patterns
- Pure color gradients or geometric shapes without labels
- Artistic borders, frames, or decorative elements
- Company logos without accompanying data
- Stock photos without text or data
- Empty placeholder images
- Pure aesthetic elements (curves, swooshes, design elements)
- Images that are clearly just for visual appeal

**Only transcribe content that contains:**
- Actual data (numbers, percentages, measurements)
- Text content (readable words, labels, titles)
- Structural information (diagrams, flowcharts, processes)
- Tables with data
- Charts with quantifiable information

**CHARTS AND GRAPHS:**

1. **PIE CHARTS**: Format as "PIE CHART - [Title if visible]" followed by data in this format:
   - Segment Name: XX% (or value if percentage not shown)
   - Example: "Sales by Region: North: 45%, South: 30%, East: 15%, West: 10%"

2. **BAR CHARTS**: Format as "BAR CHART - [Title if visible]" followed by data points:
   - Category: Value (with units if shown)
   - Example: "Revenue by Quarter: Q1: $2.5M, Q2: $3.2M, Q3: $2.8M, Q4: $4.1M"

3. **LINE CHARTS**: Format as "LINE CHART - [Title if visible]" followed by trend data:
   - Series Name: Point1(x,y), Point2(x,y), Point3(x,y)...
   - Example: "Stock Price: Jan(1,100), Feb(2,125), Mar(3,110), Apr(4,140)"

4. **SCATTER PLOTS**: Format as "SCATTER PLOT - [Title if visible]" with coordinate pairs:
   - Series: (x1,y1), (x2,y2), (x3,y3)...

**TABLES**: 
Format as proper markdown tables that are compatible with MarkItDown processing:
```
| Column1 | Column2 | Column3 |
|---------|---------|---------|
| Data1   | Data2   | Data3   |
| Data4   | Data5   | Data6   |
```

**DIAGRAMS AND FLOWCHARTS**:
- Start with "DIAGRAM - [Type]"
- Describe the flow/structure step by step
- Use arrows (→) to show connections
- Example: "Process Flow: Input → Processing → Decision → Output"

**IMAGES/PHOTOS**:
- Start with "IMAGE - [Subject]"
- Provide detailed description of what is visible
- Include any text that appears in the image
- Describe colors, objects, people, settings as relevant
- **SKIP if purely decorative or stock photo without informational content**

**TEXT CONTENT**:
- Transcribe all visible text exactly as written
- Maintain original formatting and line breaks
- Include headers, bullet points, and numbering
- Note: "TEXT CONTENT:" followed by the exact text

**DATA EXTRACTION RULES**:
1. Read all visible numbers, percentages, and labels precisely
2. If axis labels are visible, include them
3. If a legend exists, incorporate it into your description
4. Maintain the relative importance/hierarchy of information
5. If text is partially obscured, note it as "[partially visible]"
6. If you cannot read specific values, note as "[value unclear]"

**FORMATTING REQUIREMENTS**:
- Start each response with the content type (PIE CHART, TABLE, DIAGRAM, etc.)
- Use consistent formatting for similar content types
- Make the output easily parseable and structured
- Include units of measurement when visible
- Preserve any mathematical notation or formulas

**QUALITY STANDARDS**:
- Be precise with numbers and percentages
- Don't estimate or approximate unless values are unclear
- Include all visible data points, even if numerous
- Maintain the logical structure of the original content
- If multiple elements exist, separate them clearly

**CRITICAL: Before transcribing, ask yourself:**
- Does this image contain actual information, data, or text?
- Is this image meaningless?
- Is this just decorative/aesthetic?

If the answer is "just decorative", return: NO_MEANINGFUL_DATA

It is very important that you format your response correctly. Format your response as follows:
/TRANSCRIPTION_START [Your detailed transcription here OR "NO_MEANINGFUL_DATA"] /TRANSCRIPTION_END

Do not include any preambles, explanations, or comments outside of the markers. Only provide the transcription content within the specified markers.
"""

        # Prepare the chat payload
        payload = {
            "temperature": 0.1,  # Low temperature for precision
            "max_tokens": 8000,  
            "dataSources": dataSources if api_accessed else [],
            "messages": [
                {"role": "system", "content": system_prompt},
                { "role": "user",
                  "content": "Please analyze this visual content and provide a detailed transcription following the formatting guidelines. Remember to put your entire response within the /TRANSCRIPTION_START and /TRANSCRIPTION_END markers.",
                  "data" : {"dataSources": dataSources}
                },
            ],
            "options": {
                "ragOnly": False,
                "skipRag": False,
                "model": {"id": model}, 
                "prompt": "You must format your response using the specified markers. Do not include any text outside the markers. Respond only with the transcription content within /TRANSCRIPTION_START and /TRANSCRIPTION_END markers.",
                "accountId": account_data.get("account"),
                "rateLimit": account_data.get("rate_limit")
            },
        }

        logger.info("Initiating visual transcription for: %s", key)

        # Call the chat endpoint with timeout handling
        try:
            logger.debug("Calling chat endpoint for: %s", key)
            loop = asyncio.get_event_loop()
            
            # Add timeout to prevent hanging
            response, metadata = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: chat(CHAT_ENDPOINT, access_token, payload)
                ),
                timeout=180.0  # 3 minute timeout
            )
            logger.debug("Chat call completed for: %s", key)
            
        except asyncio.TimeoutError:
            logger.error("Chat call timed out after 180 seconds for: %s", key)
            return None
        except Exception as chat_error:
            logger.error("Chat call failed for %s: %s", key, str(chat_error))
            return None

        # Extract the transcription from the response using regex parsing
        if response:
            logger.debug("Transcription Response: %s", response)
            logger.debug("Received response for %s, length: %d", key, len(response))
            # Parse the transcription using regex similar to the user's pattern
            transcription_regex = (
                r"/TRANSCRIPTION_START\s*([\s\S]*?)\s*/TRANSCRIPTION_END"
            )
            transcription_match = re.search(transcription_regex, response)

            if transcription_match:
                transcription = transcription_match.group(1).strip()

                # Check if content is marked as meaningless
                if NO_SUBSTANCE in transcription:
                    logger.debug("Visual content marked as decorative/meaningless: %s", key)
                    return None

                logger.info("Visual transcription completed for: %s", key)
                return transcription
            else:
                if NO_SUBSTANCE in response:
                    logger.debug("Visual content marked as decorative/meaningless: %s", key)
                else:
                    logger.warning("No transcription markers found in response for: %s", key)
                    # Debug: Try to find any part of the markers
                    if "/TRANSCRIPTION_START" in response:
                        logger.debug("Found START marker in response")
                    if "/TRANSCRIPTION_END" in response:
                        logger.debug("Found END marker in response")
                return None
        else:
            logger.warning("No response received for visual transcription: %s", key)
            return None

    except Exception as e:
        logger.error("Error transcribing visual content: %s", str(e))
        return None


async def process_visual_for_llm(visual_data, current_user, account_data):
    """
    Complete pipeline: Save visual to S3, get transcription, and return enhanced visual_data.

    Args:
        visual_data: Dictionary containing visual content and metadata
        current_user: User ID for file organization

    Returns:
        visual_data with transcription added and binary data removed
    """
    # Some visual types have been transcribed ex. smartart in word.py
    if visual_data.get("transcription"):
        return visual_data
    try:
        # Determine visual type based on format
        visual_format = visual_data.get("format", "image/png").lower()

        # Get binary image data directly (no decoding needed)
        image_data = visual_data.get("data")
        if not image_data:
            raise Exception("No image data found in visual_data")

        # Debug: Check the type of image_data to diagnose BytesIO issue
        logger.debug("image_data type: %s", type(image_data))
        if hasattr(image_data, '__len__'):
            try:
                logger.debug("image_data length: %d", len(image_data))
            except:
                logger.debug("Could not get length of image_data")
        if isinstance(image_data, io.BytesIO):
            logger.debug("BytesIO position: %d", image_data.tell())
            logger.debug("BytesIO seekable: %s", image_data.seekable())
            logger.debug("BytesIO readable: %s", image_data.readable())

        # Save image locally for debugging first
        if SAVE_IMAGES_LOCALLY:
            save_image_locally_for_debug(image_data, "pdf_extracted")

        # Save visual to S3 (resized)
        s3_key = save_visual_to_s3(image_data, current_user)

        # Get transcription
        transcription = await transcribe_visual_content(s3_key, visual_format, account_data)

        # Clean up the temporary S3 file - ALWAYS do this regardless of transcription success
        try:
            bucket_name = os.environ.get("S3_IMAGE_INPUT_BUCKET_NAME")
            if bucket_name:
                s3.delete_object(Bucket=bucket_name, Key=s3_key)
                logger.debug("Cleaned up temporary file: %s", s3_key)
        except Exception as cleanup_error:
            logger.warning(
                "Failed to cleanup temp file %s: %s",
                s3_key, str(cleanup_error)
            )

        # Return visual_data with transcription added and data removed
        result_data = visual_data.copy()
        if not transcription:
            logger.debug("No transcription found for visual, but continuing processing")
            result_data["transcription"] = None
            return result_data

        logger.info("Transcribed visual content complete")
        result_data["transcription"] = transcription

        return result_data

    except Exception as e:
        logger.error("Error processing visual for LLM: %s", str(e))
        return None


async def batch_process_visuals(visual_map, current_user, account_data = None):
    """
    Process visuals with smart deduplication: process one of each hash + all non-hashed visuals.
    Returns all visual entries, including those with None transcriptions.
    """
    if not visual_map:
        logger.debug("No visuals to process")
        return {}
    if not account_data or 'access_token' not in account_data:
        logger.warning("No account data provided, can not process visuals")
        return {}
    # Single pass: separate unique hashed visuals from non-hashed ones
    unique_visuals = {}  # marker -> visual_data (visuals to process)
    hash_to_markers = {}  # hash -> [all markers with this hash]

    for marker, visual_data in visual_map.items():
        content_hash = visual_data.get("hash")

        if content_hash:
            # Track all markers for this hash
            if content_hash not in hash_to_markers:
                hash_to_markers[content_hash] = []
                unique_visuals[marker] = visual_data  # First occurrence
            hash_to_markers[content_hash].append(marker)
        else:
            # No hash - process individually
            unique_visuals[marker] = visual_data
            hash_to_markers[marker] = [marker]  # Self-reference

    unique_count = len(unique_visuals)
    total_count = len(visual_map)
    logger.info(
        "Processing %d unique visuals (saved %d duplicates)",
        unique_count, total_count - unique_count
    )

    # Process all unique visuals
    tasks = [
        process_visual_for_llm(visual_data, current_user, account_data)
        for visual_data in unique_visuals.values()
    ]
    markers = list(unique_visuals.keys())
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Map results back to all visual instances - keep ALL visuals, even with None transcriptions
    processed_visuals = {}

    for marker, result in zip(markers, results):
        # Handle exceptions from gather operation
        if isinstance(result, Exception):
            logger.error("Exception occurred processing visual %s: %s", marker, str(result))
            transcription = None
        # Get transcription if successful, otherwise None
        elif result and isinstance(result, dict):
            transcription = result.get("transcription")
        else:
            transcription = None

        # Apply to all instances (for hashed) or just this one (for non-hashed)
        content_hash = unique_visuals[marker].get("hash", marker)
        target_markers = hash_to_markers[content_hash]

        for target_marker in target_markers:
            enhanced_visual = visual_map[target_marker].copy()
            enhanced_visual["transcription"] = transcription  # Could be None
            processed_visuals[target_marker] = enhanced_visual

    successful_count = sum(1 for v in processed_visuals.values() if v.get("transcription"))
    logger.info("Completed: %d/%d visuals transcribed successfully", successful_count, total_count)
    logger.info("Kept all %d visual entries (including %d with None transcriptions)", total_count, total_count - successful_count)
    return processed_visuals


def save_image_locally_for_debug(image_data, filename_prefix="extracted_image"):
    """
    Save binary image data locally for debugging purposes.

    Args:
        image_data: Binary image data (bytes)
        filename_prefix: Prefix for the saved filename

    Returns:
        Local file path where the image was saved
    """
    try:
        # Safety check: Convert BytesIO or file-like objects to raw bytes immediately
        if isinstance(image_data, io.BytesIO):
            image_data.seek(0)
            image_data = image_data.getvalue()
        elif hasattr(image_data, 'read') and hasattr(image_data, 'seek'):
            image_data.seek(0)
            image_data = image_data.read()
        elif hasattr(image_data, 'getvalue'):
            image_data = image_data.getvalue()
        
        # At this point, image_data should be raw bytes
        if not isinstance(image_data, bytes):
            raise ValueError(f"Expected bytes after conversion, got {type(image_data)}")

        # Print current working directory for debugging
        logger.debug("Current working directory: %s", os.getcwd())

        # Create the directory relative to this script's location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        debug_dir = os.path.join(script_dir, "extractedImages")
        os.makedirs(debug_dir, exist_ok=True)

        # Generate unique filename with timestamp
        dt_string = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{filename_prefix}_{dt_string}.png"
        local_path = os.path.join(debug_dir, filename)

        # Create PIL Image from raw bytes (simplified since we guarantee bytes above)
        image = Image.open(io.BytesIO(image_data))
        image.save(local_path, "PNG")

        logger.debug("Image saved locally at: %s", local_path)
        logger.debug("Full path: %s", os.path.abspath(local_path))
        return local_path

    except Exception as e:
        logger.error("Error saving image locally: %s", str(e))
        return None
