import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Union, Any
import json
import mimetypes
import shutil

from agent.components.tool import register_tool


@register_tool(tags=["file_handling", "markdown"])
def convert_to_markdown(
    file_path: str,
    output_path: Optional[str] = None,
    ocr: bool = False,
    describe_images: bool = False,
    extract_images: bool = False,
    language: str = "en",
    timeout: int = 120,
):
    """
    Converts a document to markdown format using the markitdown utility.

    This tool transforms a wide variety of file formats into clean markdown,
    preserving document structure and making it suitable for use with LLMs.

    Args:
        file_path: Path to the input file to convert
        output_path: Path to save the markdown output (optional)
        ocr: Whether to apply OCR to images in the document (default: False)
        describe_images: Whether to add AI-generated descriptions of images (default: False)
        extract_images: Whether to extract images from the document (default: False)
        language: Document language code for OCR (default: "en")
        timeout: Maximum execution time in seconds (default: 120)

    Returns:
        Dictionary containing:
        - success: Boolean indicating conversion success
        - markdown: The markdown output if successful
        - output_path: Path to the output file if output_path was specified
        - metadata: Additional metadata about the document (if available)
        - images: Paths to extracted images (if extract_images=True)
        - error: Error message if conversion failed

    Examples:
        >>> convert_to_markdown('/path/to/document.pdf')
        {
            "success": true,
            "markdown": "# Document Title\n\nThis is the content of the document...",
            "metadata": {"pages": 5, "title": "Document Title", "author": "John Doe"}
        }

        >>> convert_to_markdown('/path/to/presentation.pptx', '/path/to/output.md', extract_images=True)
        {
            "success": true,
            "markdown": "# Presentation Title\n\n## Slide 1\n\n![Slide 1 Image](/path/to/images/image1.png)\n...",
            "output_path": "/path/to/output.md",
            "metadata": {"slides": 12, "title": "Presentation Title"},
            "images": ["/path/to/images/image1.png", "/path/to/images/image2.png"]
        }

        >>> convert_to_markdown('/path/to/image.jpg', ocr=True, language='fr')
        {
            "success": true,
            "markdown": "![Image](/path/to/image.jpg)\n\nText recognized from image: Le contenu extrait de l'image...",
            "metadata": {"width": 1200, "height": 800, "format": "JPEG"}
        }

    Notes:
        - Supports PDF, DOCX, PPTX, XLSX, HTML, images, audio, and more
        - The OCR option applies to images and image-based PDFs
        - When describe_images is True, AI-generated descriptions are added to images
        - For audio files, includes speech-to-text transcription
        - Extract_images saves embedded document images to a subdirectory
        - The markitdown utility must be installed on the system
        - Returns raw markdown content if output_path is not specified
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": f"Input file does not exist: {file_path}"}

    # Check if markitdown is installed
    if not shutil.which("markitdown"):
        return {
            "success": False,
            "error": "markitdown utility is not installed or not in PATH",
        }

    # Build command arguments
    command = ["markitdown"]
    command.append(file_path)

    if output_path:
        command.extend(["-o", output_path])

    if ocr:
        command.append("--ocr")

    if describe_images:
        command.append("--describe-images")

    if extract_images:
        command.append("--extract-images")

    if language != "en":
        command.extend(["--language", language])

    # Create a temporary file for metadata output
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp_metadata:
        metadata_path = temp_metadata.name

    command.extend(["--metadata", metadata_path])

    # Execute the command
    try:
        # If no output path is specified, we'll capture stdout
        if not output_path:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            stdout, stderr = process.communicate(timeout=timeout)

            if process.returncode != 0:
                return {"success": False, "error": f"Conversion failed: {stderr}"}

            markdown_content = stdout
        else:
            # If output path is specified, we'll just check the return code
            process = subprocess.Popen(
                command, stderr=subprocess.PIPE, text=True, errors="replace"
            )

            _, stderr = process.communicate(timeout=timeout)

            if process.returncode != 0:
                return {"success": False, "error": f"Conversion failed: {stderr}"}

            # Read the generated markdown file
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    markdown_content = f.read()
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to read output file: {str(e)}",
                }

        # Read metadata if available
        metadata = {}
        try:
            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
        except Exception:
            # Metadata reading failure shouldn't fail the whole operation
            pass

        # Clean up the temporary metadata file
        try:
            os.unlink(metadata_path)
        except:
            pass

        # Look for extracted images if requested
        extracted_images = []
        if extract_images:
            base_dir = (
                os.path.dirname(output_path)
                if output_path
                else os.path.dirname(file_path)
            )
            images_dir = os.path.join(base_dir, "images")

            if os.path.exists(images_dir) and os.path.isdir(images_dir):
                for image_file in os.listdir(images_dir):
                    if os.path.isfile(
                        os.path.join(images_dir, image_file)
                    ) and _is_image_file(image_file):
                        extracted_images.append(os.path.join(images_dir, image_file))

        result = {"success": True, "markdown": markdown_content, "metadata": metadata}

        if output_path:
            result["output_path"] = output_path

        if extracted_images:
            result["images"] = extracted_images

        return result

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Conversion timed out after {timeout} seconds",
        }
    except Exception as e:
        return {"success": False, "error": f"Error during conversion: {str(e)}"}


@register_tool(tags=["file_handling", "markdown"])
def batch_convert_to_markdown(
    file_paths: List[str],
    output_dir: str,
    ocr: bool = False,
    describe_images: bool = False,
    extract_images: bool = False,
    language: str = "en",
    timeout: int = 300,
):
    """
    Converts multiple documents to markdown format in a single batch operation.

    This tool processes a list of files, converting each to markdown and saving
    the results in the specified output directory.

    Args:
        file_paths: List of paths to input files to convert
        output_dir: Directory to save the markdown outputs
        ocr: Whether to apply OCR to images in the documents (default: False)
        describe_images: Whether to add AI-generated descriptions of images (default: False)
        extract_images: Whether to extract images from the documents (default: False)
        language: Document language code for OCR (default: "en")
        timeout: Maximum execution time for the entire batch in seconds (default: 300)

    Returns:
        Dictionary containing:
        - success: Boolean indicating overall batch success
        - results: Dictionary mapping input files to their conversion results
        - summary: Counts of successful and failed conversions

    Examples:
        >>> batch_convert_to_markdown(
        ...     ['/path/to/document1.pdf', '/path/to/document2.docx', '/path/to/slides.pptx'],
        ...     '/path/to/output_directory',
        ...     ocr=True
        ... )
        {
            "success": true,
            "summary": {"total": 3, "successful": 3, "failed": 0},
            "results": {
                "/path/to/document1.pdf": {
                    "success": true,
                    "output_path": "/path/to/output_directory/document1.md",
                    "metadata": {"pages": 5, "title": "Document 1"}
                },
                "/path/to/document2.docx": {
                    "success": true,
                    "output_path": "/path/to/output_directory/document2.md",
                    "metadata": {"pages": 12, "title": "Document 2"}
                },
                "/path/to/slides.pptx": {
                    "success": true,
                    "output_path": "/path/to/output_directory/slides.md",
                    "metadata": {"slides": 18, "title": "Presentation Slides"}
                }
            }
        }

        >>> batch_convert_to_markdown(
        ...     ['/path/to/good.pdf', '/path/to/nonexistent.pdf'],
        ...     '/path/to/output'
        ... )
        {
            "success": false,
            "summary": {"total": 2, "successful": 1, "failed": 1},
            "results": {
                "/path/to/good.pdf": {
                    "success": true,
                    "output_path": "/path/to/output/good.md",
                    "metadata": {"pages": 3}
                },
                "/path/to/nonexistent.pdf": {
                    "success": false,
                    "error": "Input file does not exist: /path/to/nonexistent.pdf"
                }
            }
        }

    Notes:
        - Ensures the output directory exists before processing
        - Each file maintains its original name but with a .md extension
        - Per-file timeout is calculated based on the number of files and total timeout
        - The overall batch is considered successful if at least one file converts successfully
        - Tolerates individual file failures and continues processing the remaining files
        - The results dictionary provides detailed status for each input file
    """
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to create output directory: {str(e)}",
            }

    # Calculate per-file timeout
    per_file_timeout = max(
        timeout // max(len(file_paths), 1), 30
    )  # Minimum 30 seconds per file

    results = {}
    successful_count = 0
    failed_count = 0

    for file_path in file_paths:
        # Skip if file doesn't exist
        if not os.path.exists(file_path):
            results[file_path] = {
                "success": False,
                "error": f"Input file does not exist: {file_path}",
            }
            failed_count += 1
            continue

        # Generate output path
        file_name = os.path.basename(file_path)
        base_name = os.path.splitext(file_name)[0]
        output_path = os.path.join(output_dir, f"{base_name}.md")

        # Convert the file
        result = convert_to_markdown(
            file_path=file_path,
            output_path=output_path,
            ocr=ocr,
            describe_images=describe_images,
            extract_images=extract_images,
            language=language,
            timeout=per_file_timeout,
        )

        results[file_path] = result

        if result["success"]:
            successful_count += 1
        else:
            failed_count += 1

    return {
        "success": successful_count > 0,
        "summary": {
            "total": len(file_paths),
            "successful": successful_count,
            "failed": failed_count,
        },
        "results": results,
    }


def _is_image_file(filename):
    """Helper function to detect if a file is an image based on extension."""
    image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"]
    _, ext = os.path.splitext(filename.lower())
    return ext in image_extensions


@register_tool(tags=["file_handling", "markdown"])
def convert_url_to_markdown(
    url: str,
    output_path: Optional[str] = None,
    include_images: bool = True,
    timeout: int = 180,
):
    """
    Converts a web page or online document to markdown format.

    This tool fetches content from a URL and converts it to markdown,
    handling various online document types including HTML pages,
    PDFs, and YouTube videos.

    Args:
        url: URL of the web page or document to convert
        output_path: Path to save the markdown output (optional)
        include_images: Whether to download and include images (default: True)
        timeout: Maximum execution time in seconds (default: 180)

    Returns:
        Dictionary containing:
        - success: Boolean indicating conversion success
        - markdown: The markdown output if successful
        - output_path: Path to the output file if output_path was specified
        - metadata: Additional metadata about the document (if available)
        - images: Paths to downloaded images (if include_images=True)
        - error: Error message if conversion failed

    Examples:
        >>> convert_url_to_markdown('https://example.com/article')
        {
            "success": true,
            "markdown": "# Example Article\n\nThis is the content of the web page...",
            "metadata": {"title": "Example Article", "url": "https://example.com/article"}
        }

        >>> convert_url_to_markdown('https://example.com/document.pdf', '/path/to/output.md')
        {
            "success": true,
            "markdown": "# Document Title\n\nThis is the content of the PDF...",
            "output_path": "/path/to/output.md",
            "metadata": {"pages": 5, "title": "Document Title", "url": "https://example.com/document.pdf"}
        }

        >>> convert_url_to_markdown('https://youtube.com/watch?v=abcdefghijk', include_images=False)
        {
            "success": true,
            "markdown": "# Video Title\n\nTranscription of the video content...",
            "metadata": {"title": "Video Title", "duration": "10:30", "channel": "Example Channel"}
        }

    Notes:
        - Works with HTML pages, PDFs, Office documents, and YouTube videos
        - For YouTube videos, includes transcription if available
        - Images are downloaded to a subdirectory named after the output file
        - Uses the base filename from the URL if no output_path is specified
        - The include_images option controls whether images are downloaded
        - Returns raw markdown content if output_path is not specified
        - Internet connection is required for this tool to function
    """
    # Check if markitdown is installed
    if not shutil.which("markitdown"):
        return {
            "success": False,
            "error": "markitdown utility is not installed or not in PATH",
        }

    # Build command arguments
    command = ["markitdown", url]

    if output_path:
        command.extend(["-o", output_path])

    if include_images:
        command.append("--download-images")

    # Create a temporary file for metadata output
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp_metadata:
        metadata_path = temp_metadata.name

    command.extend(["--metadata", metadata_path])

    # Execute the command
    try:
        # If no output path is specified, we'll capture stdout
        if not output_path:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            stdout, stderr = process.communicate(timeout=timeout)

            if process.returncode != 0:
                return {"success": False, "error": f"Conversion failed: {stderr}"}

            markdown_content = stdout
        else:
            # If output path is specified, we'll just check the return code
            process = subprocess.Popen(
                command, stderr=subprocess.PIPE, text=True, errors="replace"
            )

            _, stderr = process.communicate(timeout=timeout)

            if process.returncode != 0:
                return {"success": False, "error": f"Conversion failed: {stderr}"}

            # Read the generated markdown file
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    markdown_content = f.read()
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to read output file: {str(e)}",
                }

        # Read metadata if available
        metadata = {}
        try:
            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                    # Add the URL to metadata if not present
                    if "url" not in metadata:
                        metadata["url"] = url
        except Exception:
            # Metadata reading failure shouldn't fail the whole operation
            pass

        # Clean up the temporary metadata file
        try:
            os.unlink(metadata_path)
        except:
            pass

        # Look for downloaded images if requested
        downloaded_images = []
        if include_images and output_path:
            base_dir = os.path.dirname(output_path)
            base_name = os.path.splitext(os.path.basename(output_path))[0]
            images_dir = os.path.join(base_dir, f"{base_name}_images")

            if os.path.exists(images_dir) and os.path.isdir(images_dir):
                for image_file in os.listdir(images_dir):
                    if os.path.isfile(
                        os.path.join(images_dir, image_file)
                    ) and _is_image_file(image_file):
                        downloaded_images.append(os.path.join(images_dir, image_file))

        result = {"success": True, "markdown": markdown_content, "metadata": metadata}

        if output_path:
            result["output_path"] = output_path

        if downloaded_images:
            result["images"] = downloaded_images

        return result

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Conversion timed out after {timeout} seconds",
        }
    except Exception as e:
        return {"success": False, "error": f"Error during conversion: {str(e)}"}
