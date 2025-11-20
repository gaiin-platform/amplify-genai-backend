import chardet
import hashlib
import io
from PIL import Image


def is_likely_text(file_content):
    # Use chardet to detect the encoding of the file_content
    result = chardet.detect(file_content)
    confidence = result["confidence"]  # How confident chardet is about its detection
    encoding = result["encoding"]
    is_text = (
        result["encoding"] is not None and confidence > 0.7
    )  # You can adjust the confidence threshold

    return is_text, encoding


def hash_visual_data(visual_bytes):
    """
    Create a SHA-256 hash of visual content for deduplication.

    Args:
        visual_bytes: Binary data of the visual content

    Returns:
        String hash of the visual content
    """
    if not visual_bytes:
        return None

    # Create SHA-256 hash of the binary data
    hasher = hashlib.sha256()
    hasher.update(visual_bytes)
    return hasher.hexdigest()[:16]  # Use first 16 chars for shorter hash


def ensure_supported_format(image_bytes, content_type):
    """
    Convert image to supported format if necessary.
    Used by Excel, PowerPoint, and Word handlers.
    """
    # Safety check: Convert BytesIO or file-like objects to raw bytes immediately
    if isinstance(image_bytes, io.BytesIO):
        image_bytes.seek(0)
        image_bytes = image_bytes.getvalue()
    elif hasattr(image_bytes, 'read') and hasattr(image_bytes, 'seek'):
        image_bytes.seek(0)
        image_bytes = image_bytes.read()
    elif hasattr(image_bytes, 'getvalue'):
        image_bytes = image_bytes.getvalue()
    
    # At this point, image_bytes should be raw bytes
    if not isinstance(image_bytes, bytes):
        print(f"Warning: ensure_supported_format expected bytes, got {type(image_bytes)}")
        # Try to continue anyway for backward compatibility
    
    # Supported formats for vision models
    supported_formats = {
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",
        "image/png": "image/png",
        "image/gif": "image/gif",
        "image/webp": "image/webp",
    }

    # If already supported, return as-is
    if content_type in supported_formats:
        return image_bytes, supported_formats[content_type]

    # Convert unsupported formats to PNG
    try:
        with io.BytesIO(image_bytes) as input_buffer:
            img = Image.open(input_buffer)

            # Convert to RGB if necessary
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            # Save as PNG
            output_buffer = io.BytesIO()
            img.save(output_buffer, format="PNG")
            converted_bytes = output_buffer.getvalue()

        return converted_bytes, "image/png"

    except Exception as e:
        print(f"Format conversion failed for {content_type}: {e}")
        return image_bytes, content_type


def format_visual_chunk_data(visual_data, num_tokens_from_string_func):
    """
    Format visual data into a chunk for RAG processing.
    Used by Excel, PowerPoint, and Word handlers.

    Args:
        visual_data: Dictionary containing visual data
        num_tokens_from_string_func: Function to calculate tokens from string
    """
    # Only include alt text if it's actually useful
    alt_text = visual_data.get("alt_text", "")
    useful_alt_text = alt_text if is_useful_alt_text(alt_text) else ""

    alt_text_formatted = f"[{useful_alt_text}]\n" if useful_alt_text else ""
    content = f"""{visual_data['type']}: {visual_data['title']}
{visual_data['transcription']}
{alt_text_formatted}"""

    return {
        "content": content,
        "tokens": num_tokens_from_string_func(content),
        "location": visual_data["location"],
        "canSplit": True,
        "url": visual_data.get("hyperlink", ""),
    }


def is_useful_alt_text(alt_text):
    """
    Filter out auto-generated useless alt text.
    Used by Excel, PowerPoint, and Word handlers.
    """
    if not alt_text or not alt_text.strip():
        return False

    # Common auto-generated patterns to ignore
    useless_patterns = [
        "description automatically generated",
        "automatically generated",
        "chart description",
        "table description",
        "image description",
        "shape description",
        "drawing description",
        "diagram description",
        "smartart description",
        "text, logo",
        "logo, company name",
        "a picture containing",
        "image containing",
        "screenshot of a",
        "chart, line chart",
        "chart, bar chart",
        "chart, pie chart",
        "image, clipart",
        "logo description",
        "icon description",
        "person description",
        "background pattern",
    ]

    alt_lower = alt_text.lower().strip()

    # Filter out if it matches any useless pattern
    for pattern in useless_patterns:
        if pattern in alt_lower:
            return False

    # Filter out very short or generic descriptions
    if len(alt_lower) < 10:
        return False

    # Filter out if it's just single words or very basic
    generic_words = [
        "chart",
        "graph",
        "image",
        "picture",
        "shape",
        "drawing",
        "diagram",
        "photo",
        "logo",
        "icon",
        "table",
        "text",
    ]
    if alt_lower in generic_words:
        return False

    return True
