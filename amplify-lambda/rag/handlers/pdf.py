# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import io
import pypdfium2 as pdfium
import fitz  # PyMuPDF for PDF modification
from enum import Enum

from rag.handlers.text import TextExtractionHandler
from rag.handlers.shared_functions import hash_visual_data, format_visual_chunk_data


PNG = "image/png"


class VisualType(Enum):
    """Enum for visual content types we extract from PDF documents"""

    IMAGE = "Image"  # Photos, diagrams, charts rendered as raster images


class PDFHandler(TextExtractionHandler):
    def extract_text(self, file_content, visual_map={}):
        """
        Extract text from PDF. Visual markers are now handled by PyMuPDF preprocessing,
        so this method focuses on clean text extraction and visual chunk processing.
        """
        # Keep the buffer alive during the entire PDF processing
        buffer = io.BytesIO(file_content)
        pdf = pdfium.PdfDocument(buffer)

        # Preprocess visual_map to group visuals by page for efficient lookup
        visuals_by_page = {}
        for visual_marker, visual_data in visual_map.items():
            page_number = visual_data.get("location", {}).get("page_number")
            if page_number:
                if page_number not in visuals_by_page:
                    visuals_by_page[page_number] = []
                visuals_by_page[page_number].append((visual_marker, visual_data))

        chunks = []

        try:
            num_pages = len(pdf)

            for page_index in range(num_pages):
                page_number = page_index + 1

                try:
                    page = pdf[page_index]
                    textpage = page.get_textpage()

                    # Extract text from the whole page
                    # Visual markers are already embedded by PyMuPDF preprocessing
                    text = textpage.get_text_range()

                    if text and text.strip():
                        chunk = {
                            "content": text,
                            "tokens": self.num_tokens_from_string(text),
                            "location": {"page_number": page_number},
                            "canSplit": True,
                        }
                        chunks.append(chunk)

                    # Process any visuals that belong to this page for transcription
                    page_visuals = visuals_by_page.get(page_number, [])
                    for visual_marker, visual_data in page_visuals:
                        # Check if visual has been processed (has transcription)
                        if visual_data.get("transcription"):
                            # Create visual chunk using the format function
                            visual_chunk = format_visual_chunk_data(visual_data, self.num_tokens_from_string)
                            chunks.append(visual_chunk)

                    # Clean up textpage
                    textpage.close()

                except Exception as e:
                    print(f"Error processing page {page_number}: {e}")
                    continue

        finally:
            # Clean up resources
            pdf.close()
            buffer.close()

        return chunks

    ### Visual Data Extraction ###
    def preprocess_pdf_visuals(self, file_content):
        """
        Extract visual content and create a modified PDF with visual markers embedded as text.
        Uses PyMuPDF to create a valid PDF that MarkItDown can process while preserving visual markers.
        """

        # Keep the buffer alive during the entire PDF processing
        buffer = io.BytesIO(file_content)
        
        try:
            pdf = pdfium.PdfDocument(buffer)
        except Exception as e:
            print(f"Error opening PDF for visual preprocessing: {e}")
            # Return original content with empty visual map if PDF can't be opened
            return file_content, {}

        visual_map = {}

        try:
            # Process each page to extract visuals
            num_pages = len(pdf)
            
            for page_index in range(num_pages):
                page_number = page_index + 1
                try:
                    page = pdf[page_index]
                    page_visuals = self.extract_page_visuals(page, page_number)
                    visual_map.update(page_visuals)
                except Exception as e:
                    print(f"Error processing page {page_number}: {e}")
                    continue

        finally:
            # Clean up resources
            pdf.close()
            buffer.close()

        print(f"[DEBUG] Extracted {len(visual_map)} visual markers from PDF")
        
        if not visual_map:
            # No visuals found, return original content
            return file_content, {}
        
        # Create a modified PDF with visual markers embedded as text using PyMuPDF
        modified_content = self._create_pdf_with_visual_markers_pymupdf(file_content, visual_map)
        
        return modified_content, visual_map

    def _create_pdf_with_visual_markers_pymupdf(self, file_content, visual_map):
        """
        Create a modified PDF with visual markers embedded as text using PyMuPDF.
        Places markers in logical text flow positions rather than exact image coordinates.
        """
        try:
            # Open the PDF with PyMuPDF
            doc = fitz.open(stream=file_content, filetype="pdf")
            
            # Group visuals by page for efficient processing
            visuals_by_page = {}
            for marker, visual_data in visual_map.items():
                page_number = visual_data.get("location", {}).get("page_number")
                if page_number:
                    if page_number not in visuals_by_page:
                        visuals_by_page[page_number] = []
                    visuals_by_page[page_number].append((marker, visual_data))
            
            # Add text annotations to each page
            for page_number, page_visuals in visuals_by_page.items():
                try:
                    page = doc[page_number - 1]  # Convert to 0-based index
                    
                    # Get text blocks to understand text flow
                    text_blocks = page.get_text("dict")["blocks"]
                    
                    for marker, visual_data in page_visuals:
                        location = visual_data.get("location", {})
                        bounds = location.get("bounds")
                        
                        if bounds and len(bounds) >= 4:
                            left, bottom, right, top = bounds
                            
                            # Find the best text insertion point near the image
                            insertion_point = self._find_text_flow_insertion_point(
                                text_blocks, bounds, page.rect.width, page.rect.height
                            )
                            
                            # Add the marker as text with a distinctive style
                            page.insert_text(
                                insertion_point, 
                                f"\n{marker}\n", 
                                fontsize=10,
                                color=(0, 0, 1),  # Blue color
                                fontname="helv"   # Helvetica font
                            )
                            
                            # print(f"[DEBUG] Added text annotation {marker} at text flow position {insertion_point} on page {page_number}")
                        else:
                            # No bounds, add at top of page
                            point = fitz.Point(50, 50)  # Top-left with margin
                            page.insert_text(
                                point, 
                                f"\n{marker}\n", 
                                fontsize=10,
                                color=(0, 0, 1),
                                fontname="helv"
                            )
                            # print(f"[DEBUG] Added text annotation {marker} at default position on page {page_number}")
                            
                except Exception as e:
                    print(f"Error adding text to page {page_number}: {e}")
                    continue
            
            # Save the modified PDF to bytes
            modified_pdf_bytes = doc.write()
            doc.close()
            
            print(f"Created modified PDF with {len(visual_map)} visual markers embedded as text")
            
            return modified_pdf_bytes
            
        except Exception as e:
            print(f"[DEBUG] Error creating modified PDF with PyMuPDF: {e}")
            # Fallback to original content if modification fails
            return file_content

    def _find_text_flow_insertion_point(self, text_blocks, image_bounds, page_width, page_height):
        """
        Find the best position to insert visual marker in the reading order.
        Analyzes text blocks above and below the image to place marker between them.
        """
        try:
            left, bottom, right, top = image_bounds
            image_center_y = (top + bottom) / 2
            
            # Separate text blocks into those above and below the image
            blocks_above = []  # Y coordinates higher than image (above in visual terms)
            blocks_below = []  # Y coordinates lower than image (below in visual terms)
            
            for block in text_blocks:
                if block.get("type") == 0:  # Text block
                    block_bbox = block.get("bbox", [0, 0, 0, 0])
                    block_left, block_bottom, block_right, block_top = block_bbox
                    block_center_y = (block_bottom + block_top) / 2
                    
                    # Get text content for debugging
                    block_text = ""
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                block_text += span.get("text", "")
                    
                    # In PDF coordinates, higher Y values are at the top
                    if block_center_y > image_center_y + 20:  # Block is above image (higher Y)
                        blocks_above.append((block_center_y, block_bbox, block_text))
                    elif block_center_y < image_center_y - 20:  # Block is below image (lower Y)
                        blocks_below.append((block_center_y, block_bbox, block_text))
            
            # Sort blocks: above blocks by Y descending (closest to image first)
            # below blocks by Y ascending (closest to image first)
            blocks_above.sort(key=lambda x: x[0], reverse=True)
            blocks_below.sort(key=lambda x: x[0], reverse=False)
            
            # Strategy: Place marker between the closest blocks above and below
            if blocks_above and blocks_below:
                # Find the gap between the closest blocks
                closest_above = blocks_above[0]  # Lowest Y among above blocks
                closest_below = blocks_below[0]  # Highest Y among below blocks
                
                above_y = closest_above[0]
                below_y = closest_below[0]
                
                # Place marker in the middle of the gap
                insertion_y = (above_y + below_y) / 2
                
                # Use left margin for X position
                insertion_x = max(50, left)
                
                return fitz.Point(insertion_x, insertion_y)
            
            elif blocks_above:
                # Only blocks above - place marker below the lowest one
                lowest_above = blocks_above[0]
                above_bbox = lowest_above[1]
                insertion_x = max(50, left)
                insertion_y = above_bbox[1] - 20  # Below the text block
                
                return fitz.Point(insertion_x, insertion_y)
            
            elif blocks_below:
                # Only blocks below - place marker below the lowest one (at the end)
                lowest_below = blocks_below[-1]  # Last in sorted list = lowest Y position
                below_bbox = lowest_below[1]
                insertion_x = max(50, left)
                insertion_y = below_bbox[1] - 20  # Below the lowest text block
                
                return fitz.Point(insertion_x, insertion_y)
            
            # Fallback: No suitable text blocks found
            insertion_x = max(50, left)
            insertion_y = top - 20  # Just above the image
            
            return fitz.Point(insertion_x, insertion_y)
            
        except Exception as e:
            print(f"[DEBUG] Error finding text flow insertion point: {e}")
            # Ultimate fallback
            return fitz.Point(50, 100)

    def extract_page_visuals(self, page, page_number):
        """Extract all visual content from a single PDF page"""

        visuals = {}
        # Count each visual type we extract
        type_counters = {
            VisualType.IMAGE.value: 0,
        }

        try:
            # Get page dimensions for coordinate context
            page_width = page.get_width()
            page_height = page.get_height()

            # Extract Images
            image_visuals = self.extract_page_images(
                page, page_number, page_width, page_height, type_counters
            )
            visuals.update(image_visuals)

        except Exception as e:
            print(f"Error extracting visuals from page {page_number}: {e}")

        return visuals

    def extract_page_images(
        self, page, page_number, page_width, page_height, type_counters
    ):
        """Extract raster images from PDF page"""
        visuals = {}

        try:
            objects = page.get_objects()

            for obj in objects:
                if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                    type_counters[VisualType.IMAGE.value] += 1
                    marker = f"<Visual#{page_number}_{VisualType.IMAGE.value}_{type_counters[VisualType.IMAGE.value]}>"

                    visual_data = self.extract_image_object_data(
                        obj, page_number, page_width, page_height
                    )
                    if visual_data:
                        visuals[marker] = visual_data

        except Exception as e:
            print(f"Error extracting images from page {page_number}: {e}")

        return visuals

    def extract_image_object_data(
        self, image_obj, page_number, page_width, page_height
    ):
        """Extract data from PDF image object with improved error handling"""

        try:
            # Get image bounds for positioning
            bounds = None
            try:
                bounds = image_obj.get_pos()
            except Exception as e:
                print(f"Could not get image bounds on page {page_number}: {e}")
                # Use default bounds if we can't get actual ones
                bounds = [0, 0, page_width * 0.5, page_height * 0.5]

            # Extract actual image data from PDF
            image_bytes = None
            original_format = None

            try:
                # Use BytesIO buffer to extract image data
                buffer = io.BytesIO()
                
                # Try to extract image data with error handling
                try:
                    image_obj.extract(buffer, fb_format="PNG")
                    image_bytes = buffer.getvalue()
                    buffer.close()
                    
                    if image_bytes and len(image_bytes) > 0:
                        original_format = "image/png"
                    else:
                        print(f"No image data extracted from PDF object on page {page_number}")
                        return None
                        
                except Exception as extract_error:
                    print(f"Could not extract image data from PDF object on page {page_number}: {extract_error}")
                    buffer.close()
                    return None
                    
            except Exception as buffer_error:
                print(f"Buffer error during image extraction on page {page_number}: {buffer_error}")
                return None

            # Generate hash for deduplication
            try:
                content_hash = hash_visual_data(image_bytes)
            except Exception as hash_error:
                print(f"Could not generate hash for image on page {page_number}: {hash_error}")
                content_hash = f"image_{page_number}_{hash(str(bounds))}"

            # Calculate position and size
            try:
                position_info = self.calculate_position_info(bounds, page_width, page_height)
            except Exception as pos_error:
                print(f"Could not calculate position info for image on page {page_number}: {pos_error}")
                position_info = {"region": "unknown"}

            return {
                "type": VisualType.IMAGE.value,
                "format": original_format,
                "data": image_bytes,
                "hash": content_hash,
                "location": {
                    "page_number": page_number,
                },
                "alt_text": "",
                "title": f"Image on page {page_number}",
                "hyperlink": "",
            }

        except Exception as e:
            print(f"Image extraction failed on page {page_number}: {e}")
            # Return a placeholder visual data instead of None to maintain marker consistency
            return {
                "type": VisualType.IMAGE.value,
                "format": "image/png",
                "data": b"",  # Empty bytes
                "hash": f"placeholder_{page_number}",
                "location": {
                    "page_number": page_number
                },
                "alt_text": "",
                "title": f"Image on page {page_number} (extraction failed)",
                "hyperlink": "",
            }

    def calculate_position_info(self, bounds, page_width, page_height):
        """Calculate human-readable position information"""
        if not bounds or len(bounds) < 4:
            return {"region": "unknown"}

        try:
            left, bottom, right, top = bounds

            # Calculate center point
            center_x = (left + right) / 2
            center_y = (bottom + top) / 2

            # Determine region (top/middle/bottom + left/center/right)
            vertical_region = (
                "top"
                if center_y > page_height * 0.66
                else "middle" if center_y > page_height * 0.33 else "bottom"
            )

            horizontal_region = (
                "left"
                if center_x < page_width * 0.33
                else "center" if center_x < page_width * 0.66 else "right"
            )

            # Calculate size relative to page
            width = abs(right - left)
            height = abs(top - bottom)
            size_ratio = (width * height) / (page_width * page_height)

            size_category = (
                "large"
                if size_ratio > 0.25
                else "medium" if size_ratio > 0.1 else "small"
            )

            return {
                "region": f"{vertical_region}-{horizontal_region}",
                "center": (center_x, center_y),
                "size": size_category,
                "bounds_normalized": (
                    left / page_width,
                    bottom / page_height,
                    right / page_width,
                    top / page_height,
                ),
            }
        except:
            return {"region": "unknown"}
