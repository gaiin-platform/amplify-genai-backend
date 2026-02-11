"""
Selective Visual Processing
Process only important visuals with LLM, use OCR for simple images
3.3X faster than processing all visuals with LLM
"""

import asyncio
from pycommon.logger import getLogger

logger = getLogger("selective_visual")


class VisualImportance:
    """Importance score thresholds"""
    HIGH = 50  # Process with LLM vision API
    MEDIUM = 30  # Process with OCR
    LOW = 0  # Skip or minimal processing


async def batch_process_visuals_selective(visual_map, current_user, account_data=None):
    """
    Selective visual processing: Only process important visuals with expensive LLM

    Strategy:
    1. Classify visuals by importance (size, caption, complexity, position)
    2. High priority → LLM vision API (accurate semantic understanding)
    3. Low priority → OCR only (fast text extraction)
    4. Very low → Skip entirely

    Expected speedup: 3.3X (process 30% with LLM vs 100%)

    Args:
        visual_map: Dict of {marker: visual_data}
        current_user: User identifier
        account_data: Account credentials for API calls

    Returns:
        Dict of {marker: processed_visual}
    """

    if not visual_map:
        logger.debug("No visuals to process")
        return {}

    if not account_data or 'access_token' not in account_data:
        logger.warning("No account data provided, cannot process visuals")
        return {}

    logger.info(f"Starting selective visual processing for {len(visual_map)} visuals")

    # STEP 1: Classify all visuals by importance
    classified_visuals = {}

    for marker, visual_data in visual_map.items():
        importance_score = classify_visual_importance(visual_data)
        processing_method = get_processing_method(importance_score)

        classified_visuals[marker] = {
            "data": visual_data,
            "importance": importance_score,
            "method": processing_method
        }

    # Count by method
    llm_count = sum(1 for v in classified_visuals.values() if v["method"] == "llm_vision")
    ocr_count = sum(1 for v in classified_visuals.values() if v["method"] == "ocr")
    skip_count = sum(1 for v in classified_visuals.values() if v["method"] == "skip")

    logger.info(f"Visual classification complete:")
    logger.info(f"  - LLM vision: {llm_count} ({llm_count/len(visual_map)*100:.1f}%)")
    logger.info(f"  - OCR only: {ocr_count} ({ocr_count/len(visual_map)*100:.1f}%)")
    logger.info(f"  - Skip: {skip_count} ({skip_count/len(visual_map)*100:.1f}%)")

    # STEP 2: Process high-priority visuals with LLM (expensive but accurate)
    high_priority_markers = [
        marker for marker, info in classified_visuals.items()
        if info["method"] == "llm_vision"
    ]

    # Import the original LLM processing function
    from rag.handlers.visual_to_text import process_visual_for_llm

    llm_tasks = [
        process_visual_for_llm(
            classified_visuals[marker]["data"],
            current_user,
            account_data
        )
        for marker in high_priority_markers
    ]

    logger.info(f"Processing {len(llm_tasks)} high-priority visuals with LLM...")
    llm_results = await asyncio.gather(*llm_tasks, return_exceptions=True)

    # STEP 3: Process medium-priority visuals with OCR (fast)
    medium_priority_markers = [
        marker for marker, info in classified_visuals.items()
        if info["method"] == "ocr"
    ]

    ocr_tasks = [
        process_visual_with_ocr(classified_visuals[marker]["data"])
        for marker in medium_priority_markers
    ]

    logger.info(f"Processing {len(ocr_tasks)} medium-priority visuals with OCR...")
    ocr_results = await asyncio.gather(*ocr_tasks, return_exceptions=True)

    # STEP 4: Merge results
    processed_visuals = {}

    # Add LLM results
    for marker, result in zip(high_priority_markers, llm_results):
        if isinstance(result, Exception):
            logger.error(f"LLM processing failed for {marker}: {result}")
            transcription = None
        elif result and isinstance(result, dict):
            transcription = result.get("transcription")
        else:
            transcription = None

        enhanced_visual = visual_map[marker].copy()
        enhanced_visual["transcription"] = transcription
        enhanced_visual["processing_method"] = "llm_vision"
        enhanced_visual["importance_score"] = classified_visuals[marker]["importance"]
        processed_visuals[marker] = enhanced_visual

    # Add OCR results
    for marker, result in zip(medium_priority_markers, ocr_results):
        if isinstance(result, Exception):
            logger.error(f"OCR processing failed for {marker}: {result}")
            transcription = None
        elif result and isinstance(result, dict):
            transcription = result.get("transcription")
        else:
            transcription = None

        enhanced_visual = visual_map[marker].copy()
        enhanced_visual["transcription"] = transcription
        enhanced_visual["processing_method"] = "ocr"
        enhanced_visual["importance_score"] = classified_visuals[marker]["importance"]
        processed_visuals[marker] = enhanced_visual

    # Add skipped visuals (with None transcription)
    skip_markers = [
        marker for marker, info in classified_visuals.items()
        if info["method"] == "skip"
    ]

    for marker in skip_markers:
        enhanced_visual = visual_map[marker].copy()
        enhanced_visual["transcription"] = None
        enhanced_visual["processing_method"] = "skipped"
        enhanced_visual["importance_score"] = classified_visuals[marker]["importance"]
        processed_visuals[marker] = enhanced_visual

    successful_count = sum(1 for v in processed_visuals.values() if v.get("transcription"))
    logger.info(f"Selective processing complete: {successful_count}/{len(visual_map)} visuals transcribed")

    return processed_visuals


def classify_visual_importance(visual_data):
    """
    Score visual importance (0-100)

    High score (50+) = important (charts, diagrams, complex images)
    Medium score (30-49) = moderately important (text-heavy images)
    Low score (<30) = decorative (logos, icons, simple graphics)

    Args:
        visual_data: Dict with visual properties (width, height, caption, etc.)

    Returns:
        int: Importance score (0-100)
    """
    score = 0

    # SIZE HEURISTIC: Large images are usually important
    width = visual_data.get('width', 0)
    height = visual_data.get('height', 0)
    area = width * height

    if area > 200000:  # Very large
        score += 35
    elif area > 100000:  # Large
        score += 25
    elif area > 50000:  # Medium
        score += 15
    # Small images (<50k pixels) get 0 points

    # CAPTION HEURISTIC: Captioned images are important
    if visual_data.get('has_caption'):
        score += 30
    # Check for figure/table references nearby
    elif visual_data.get('has_figure_reference'):
        score += 25

    # COMPLEXITY HEURISTIC: Complex images (charts, diagrams) are important
    # Entropy is a measure of visual complexity
    entropy = visual_data.get('entropy', 0)
    if entropy > 6.0:  # High complexity
        score += 20
    elif entropy > 4.0:  # Medium complexity
        score += 10

    # POSITION HEURISTIC: In-body images are more important than headers/footers
    if not visual_data.get('in_header_or_footer', False):
        score += 10

    # IMAGE TYPE HEURISTIC: Certain types are more important
    # This could be detected from image analysis or filename
    visual_type = visual_data.get('type', 'unknown').lower()

    if any(keyword in visual_type for keyword in ['chart', 'graph', 'diagram', 'plot']):
        score += 25
    elif any(keyword in visual_type for keyword in ['table', 'matrix']):
        score += 20
    elif any(keyword in visual_type for keyword in ['logo', 'icon', 'decoration']):
        score -= 20  # Definitely low priority

    # Ensure score is in valid range
    score = max(0, min(100, score))

    return score


def get_processing_method(importance_score):
    """
    Determine processing method based on importance score

    Args:
        importance_score: Score from classify_visual_importance

    Returns:
        str: "llm_vision", "ocr", or "skip"
    """
    if importance_score >= VisualImportance.HIGH:
        return "llm_vision"  # Expensive but accurate
    elif importance_score >= VisualImportance.MEDIUM:
        return "ocr"  # Fast text extraction
    else:
        return "skip"  # Very low value, skip processing


async def process_visual_with_ocr(visual_data):
    """
    Fast OCR-only processing (0.5-2s vs 5-30s for LLM)

    Uses Tesseract or similar OCR engine to extract text
    Does not provide semantic understanding like LLM

    Args:
        visual_data: Dict with visual properties including image bytes

    Returns:
        dict: {"transcription": str, "method": "ocr", "confidence": str}
    """
    try:
        # Try to import OCR library
        try:
            import pytesseract
            from PIL import Image
            import io
        except ImportError:
            logger.warning("pytesseract not available, OCR disabled")
            return {"transcription": "[OCR not available]", "method": "ocr", "confidence": "none"}

        # Get image bytes
        image_bytes = visual_data.get('image')
        if not image_bytes:
            return {"transcription": None, "method": "ocr", "confidence": "none"}

        # Convert to PIL Image
        image = Image.open(io.BytesIO(image_bytes))

        # Extract text with OCR
        text = pytesseract.image_to_string(image)

        # Clean up text
        text = text.strip()

        if not text:
            return {"transcription": "[No text detected]", "method": "ocr", "confidence": "low"}

        # Format transcription
        transcription = f"OCR extracted text: {text}"

        return {
            "transcription": transcription,
            "method": "ocr",
            "confidence": "medium"
        }

    except Exception as e:
        logger.error(f"OCR processing failed: {str(e)}")
        return {
            "transcription": f"[OCR error: {str(e)}]",
            "method": "ocr",
            "confidence": "none"
        }
