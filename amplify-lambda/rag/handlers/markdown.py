# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
from rag.handlers.text import TextExtractionHandler
from rag.handlers.shared_functions import format_visual_chunk_data
class MarkDownHandler(TextExtractionHandler):
    """
    Handler for processing Markdown content, removing unnecessary markdown syntax
    while preserving the semantic structure and meaning of the content.
    """

    def extract_text(self, file_content, key, visual_map={}):
        """
        Extract text from Markdown content, cleaning markdown syntax as appropriate.

        Args:
            file_content: The binary content of the file
            key: The file key to determine the source format
            visual_map: A map of visual content transcribed

        Returns:
            A list of dicts containing the cleaned content with structure information
        """
        try:
            # Initialize current slide tracking
            self.current_slide = None
            
            # Decode the markdown content to string
            if isinstance(file_content, bytes):
                text = file_content.decode("utf-8")
            else:
                text = file_content

            # Check if this content has slides before cleaning
            has_slides = self._detect_slides(text)

            # Apply markdown cleaning operations
            cleaned_text = self._clean_markdown(text)

            # Split into paragraphs (blank line separation)
            paragraphs = [
                p.strip() for p in re.split(r"\n\s*\n", cleaned_text) if p.strip()
            ]

            chunks = []
            line_number = 1
            processed_visual_markers = set()
            
            for i, paragraph in enumerate(paragraphs):
                # Skip empty paragraphs
                if not paragraph.strip():
                    continue

                # Check if this paragraph contains ONLY visual markers (no other text)
                marker_pattern = r'<Visual#[^>]+>'
                markers_in_paragraph = re.findall(marker_pattern, paragraph)
                cleaned_paragraph = self._remove_visual_markers_from_text(paragraph)
                
                if markers_in_paragraph and not cleaned_paragraph.strip():
                    # This paragraph contains only visual markers - add them immediately
                    for marker_key in markers_in_paragraph:
                        processed_visual_markers.add(marker_key)
                        visual_data = visual_map.get(marker_key)
                        if visual_data:
                            visual_chunk = self._format_visual_chunk_data(key, visual_data)
                            chunks.append(visual_chunk)
                    continue
                
                # Process paragraphs with mixed content (text + markers) or text only
                paragraph_chunks = self._process_paragraph(paragraph, visual_map, processed_visual_markers, key)
                
                # Add all chunks from this paragraph in order
                for chunk in paragraph_chunks:
                    # Add dynamic location info
                    if "location" not in chunk:
                        chunk["location"] = {}
                    
                    # Only add paragraph/line numbers if this isn't slide-based content
                    if not has_slides:
                        chunk["location"]["paragraph_number"] = i + 1
                        chunk["location"]["line_number"] = line_number
                    
                    chunks.append(chunk)
                
                # Update line number for next paragraph
                line_number += paragraph.count("\n") + 2

            # Add any completely unprocessed visuals at the end (only if they were never found in text)
            for marker_key, visual_data in visual_map.items():
                if marker_key not in processed_visual_markers:
                    visual_chunk = self._format_visual_chunk_data(key, visual_data)
                    chunks.append(visual_chunk)
            print("Markdown chunks:\n", chunks)
            return chunks

        except Exception as e:
            print(f"Error processing markdown file: {str(e)}")
            return []

    def _format_visual_chunk_data(self, key, visual_data):
        if key.endswith((".pdf", ".docx", ".pptx", ".xls", ".xlsx")):
            # All these file types use the same visual processing
            return format_visual_chunk_data(visual_data, self.num_tokens_from_string)
        else:
            # Text files, etc.
            content = visual_data["transcription"]
            return {
                "content": content,
                "tokens": self.num_tokens_from_string(content),
                "location": visual_data.get("location"),
                "canSplit": True,
            }

    def _detect_slides(self, text):
        """
        Detect if the content contains slide markers.

        Args:
            text: The original text content

        Returns:
            Boolean indicating if slides are present
        """
        # Look for HTML comment style slide markers: <!-- Slide number: 1 -->
        if re.search(r"<!--\s*Slide\s+number:\s*\d+\s*-->", text, re.IGNORECASE):
            return True

        # Look for already processed slide markers: [Slide 1]
        if re.search(r"\[Slide\s+\d+\]", text, re.IGNORECASE):
            return True

        return False

    def _extract_slide_from_paragraph(self, paragraph):
        """
        Extract slide number from a paragraph if it contains a slide marker.

        Args:
            paragraph: The paragraph text to check

        Returns:
            Slide number (int) if found, None otherwise
        """
        # Look for [Slide X] pattern in the paragraph
        match = re.search(r"\[Slide\s+(\d+)\]", paragraph, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return None

    def _clean_markdown(self, text):
        """
        Clean markdown syntax while preserving semantic meaning.
        CRITICAL: Visual markers like <Visual#1_Image_1> must pass through completely unchanged.

        Args:
            text: Original markdown text

        Returns:
            Cleaned text with markdown syntax removed but visual markers preserved
        """

        # HEADERS: Convert headers to plain text but preserve their prominence
        # Replace ATX-style headers (# Header)
        text = re.sub(r"^#{1,6}\s+(.*?)$", r"\1", text, flags=re.MULTILINE)

        # Replace Setext-style headers (Header\n======)
        text = re.sub(r"^(.*?)\n[=]{2,}$", r"\1", text, flags=re.MULTILINE)
        text = re.sub(r"^(.*?)\n[-]{2,}$", r"\1", text, flags=re.MULTILINE)

        # EMPHASIS: Remove emphasis markers but keep the content
        # Bold: **text** or __text__
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"__(.*?)__", r"\1", text)

        # Italic: *text* or _text_
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        # For underscores, we need to avoid matching inside visual markers
        # Split on visual markers, process each part separately, then rejoin
        parts = re.split(r'(<Visual#[^>]*>)', text)
        for i in range(len(parts)):
            if not parts[i].startswith('<Visual#'):
                # Only process non-visual-marker parts
                parts[i] = re.sub(r"_(.*?)_", r"\1", parts[i])
        text = ''.join(parts)

        # Bold-italic: ***text*** or ___text___
        text = re.sub(r"\*\*\*(.*?)\*\*\*", r"\1", text)
        text = re.sub(r"___(.*?)___", r"\1", text)

        # LISTS: Preserve list structure
        # Unordered lists: -, +, * - Convert to bullet points
        text = re.sub(r"^\s*[-+*]\s+(.*?)$", r"• \1", text, flags=re.MULTILINE)

        # Ordered lists: 1., 2., etc. - Preserve the numbers
        text = re.sub(r"^\s*(\d+)\.\s+(.*?)$", r"\1. \2", text, flags=re.MULTILINE)

        # CODE: Keep code content without the backticks
        # Inline code: `code`
        text = re.sub(r"`(.*?)`", r"\1", text)

        # Code blocks: ```language\ncode\n```
        text = re.sub(r"```[\w]*\n(.*?)\n```", r"\1", text, flags=re.DOTALL)

        # Indented code blocks
        text = re.sub(r"^( {4}|\t)(.*?)$", r"\2", text, flags=re.MULTILINE)

        # LINKS: Keep the link text and add the URL in parentheses if it adds context
        # [text](url) -> text (url)
        text = re.sub(
            r"\[(.*?)\]\((.*?)\)",
            lambda m: (
                f"{m.group(1)} ({m.group(2)})"
                if m.group(1).lower() != m.group(2).lower()
                else m.group(1)
            ),
            text,
        )

        # IMAGES: Convert to text description
        # ![alt text](url) -> [Image: alt text]
        text = re.sub(r"!\[(.*?)\]\(.*?\)", r"[Image: \1]", text)

        # BLOCKQUOTES: Remove '>' but keep the content
        # > quote -> quote
        text = re.sub(r"^\s*>\s+(.*?)$", r"\1", text, flags=re.MULTILINE)

        # HORIZONTAL RULES: Replace with a line break
        # ---, ***, ___ -> (empty line)
        text = re.sub(r"^\s*([*\-_])\s*(\1\s*){2,}$", r"", text, flags=re.MULTILINE)

        # TABLES: Optimize table structure for LLM processing
        # Check if there are tables in the text (look for typical markdown table patterns)
        if re.search(r"^\s*\|.*\|\s*$", text, flags=re.MULTILINE) or re.search(
            r"^\s*\|[-:|]+\|", text, flags=re.MULTILINE
        ):
            # Process the markdown tables into a more structured format
            lines = text.split("\n")
            new_lines = []
            in_table = False
            header_processed = False
            header_row = []
            current_table = []

            for line in lines:
                # Detect table rows (lines with multiple | characters)
                if re.match(r"^\s*\|.*\|\s*$", line) and "|" in line[1:]:
                    if not in_table:
                        in_table = True
                        new_lines.append("\n")

                    # Skip separator rows (e.g., |---|---|)
                    if re.match(r"^\s*\|?\s*[-:]+[-| :]+\s*\|?\s*$", line):
                        continue

                    # Process cells
                    # Split by pipe character and remove leading/trailing pipes
                    row_cells = line.strip().split("|")
                    if row_cells[0].strip() == "":
                        row_cells = row_cells[1:]
                    if row_cells and row_cells[-1].strip() == "":
                        row_cells = row_cells[:-1]

                    # Clean cell content
                    cells = [cell.strip() for cell in row_cells]

                    # If this is the first row and we haven't processed a header yet,
                    # treat it as the header row
                    if not header_processed:
                        # Strip out "Unnamed: X" placeholders from headers
                        header_row = []
                        for cell in cells:
                            # Check if the cell matches the "Unnamed: X" pattern and replace with empty string
                            if re.match(r"^Unnamed:\s*\d+$", cell):
                                header_row.append("")
                            else:
                                header_row.append(cell)

                        # Format header as CSV
                        csv_header = ",".join(
                            [self._escape_csv_cell(cell) for cell in header_row]
                        )
                        new_lines.append(csv_header)
                        header_processed = True
                        continue

                    # Format data rows as CSV
                    row_data = []
                    for i in range(len(header_row)):
                        if i < len(cells):
                            row_data.append(self._escape_csv_cell(cells[i]))
                        else:
                            row_data.append("")  # Empty value for missing cells

                    new_lines.append(",".join(row_data))
                    current_table.append(cells)
                else:
                    # When we exit a table, add a marker
                    if in_table:
                        in_table = False
                        header_processed = False
                        current_table = []
                        new_lines.append("\n\n")

                    # Add non-table line
                    new_lines.append(line)

            # If we were still in a table at the end, close it
            if in_table:
                new_lines.append("\n\n")

            # Replace the text with our processed version
            text = "\n".join(new_lines)
        else:
            # If there are no tables, just use the more basic table cleanup
            # Remove leading/trailing |
            text = re.sub(r"^\s*\|\s*(.*?)\s*\|\s*$", r"\1", text, flags=re.MULTILINE)

            # Remove table separators (rows with |----|----| pattern)
            text = re.sub(
                r"^\s*\|?\s*[-:]+[-| :]+\s*\|?\s*$", r"", text, flags=re.MULTILINE
            )

            # Clean up remaining table cells
            text = re.sub(r"\|\s*", r" | ", text)

        # ESCAPE CHARACTERS: Replace escaped characters with their literal form
        # \*, \_, etc. -> *, _, etc.
        text = re.sub(r"\\([\\`*_{}[\]()#+\-.!|])", r"\1", text)

        # HTML TAGS: Remove simple HTML tags but preserve visual markers
        # Exclude anything that looks like <Visual#...>
        text = re.sub(r"</?(?!Visual#)[a-z][a-z0-9]*[^<>]*>", r"", text, flags=re.IGNORECASE)
        
        # Handle HTML comments, with special treatment for slide numbers
        text = re.sub(
            r"<!--\s*Slide\s+number:\s*(\d+)\s*-->",
            r"[Slide \1]",
            text,
            flags=re.IGNORECASE,
        )
        # Remove any other HTML comments
        text = re.sub(r"<!--.*?-->", r"", text, flags=re.DOTALL)

        # Clean up excessive whitespace
        # Replace multiple spaces with a single space
        text = re.sub(r" {2,}", " ", text)

        # Normalize line breaks
        text = re.sub(r"\r\n|\r", "\n", text)

        # Remove excessive line breaks (more than 2)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Final trim
        text = text.strip()

        return text

    def _escape_csv_cell(self, cell):
        """
        Properly escape a cell value for CSV format.

        Args:
            cell: The cell content to escape

        Returns:
            Properly escaped cell value for CSV
        """
        if cell is None:
            return ""

        # Replace double quotes with two double quotes (CSV escaping)
        cell = str(cell).replace('"', '""')

        # If the cell contains commas, quotes, or newlines, wrap in quotes
        if any(char in cell for char in ',"\n\r'):
            return f'"{cell}"'

        return cell

    def _process_paragraph(self, paragraph, visual_map, processed_markers, key):
        """
        Process a single paragraph for visual markers and return a list of chunks.
        Returns chunks in the order they appear in the paragraph.
        """
        chunks = []
        
        # Split the paragraph by visual markers while keeping the markers
        parts = re.split(r'(<Visual#[^>]+>)', paragraph)
        
        for part in parts:
            if part.startswith('<Visual#'):
                # This is a visual marker
                marker_key = part
                processed_markers.add(marker_key)
                
                # Find the corresponding visual in the visual map
                visual_data = visual_map.get(marker_key)
                if visual_data:
                    # Create visual chunk using the proper format function
                    visual_chunk = self._format_visual_chunk_data(key, visual_data)
                    chunks.append(visual_chunk)
            else:
                # This is text content - check for slide markers
                cleaned_text = part.strip()
                if cleaned_text:
                    # Extract slide number if present
                    slide_number = self._extract_slide_from_paragraph(cleaned_text)
                    
                    # Update current slide if we found a new slide marker
                    if slide_number is not None:
                        self.current_slide = slide_number
                    
                    # ALWAYS remove ALL slide markers from the text (not just when slide_number is found)
                    # This handles cases where slide markers appear in middle of paragraphs
                    cleaned_text = re.sub(r'\[Slide\s+\d+\]\s*', '', cleaned_text, flags=re.IGNORECASE).strip()
                    
                    # Only create a chunk if there's actual content after removing slide markers
                    if cleaned_text:
                        text_chunk = {
                            "content": cleaned_text,
                            "tokens": self.num_tokens_from_string(cleaned_text),
                            "location": {},  # Will be filled in by extract_text
                            "canSplit": True,
                        }
                        
                        # Add current slide number to location (either from marker or current state)
                        if self.current_slide is not None:
                            text_chunk["location"]["slide_number"] = self.current_slide
                        
                        chunks.append(text_chunk)
        
        return chunks

    def _remove_visual_markers_from_text(self, text):
        """
        Remove visual markers from text content
        """
        # Pattern: ex. <Visual#sheet_visualtype_number>
        marker_pattern = r'<Visual#[^>]+>'
        cleaned_text = re.sub(marker_pattern, '', text)
        
        # Clean up any extra whitespace left behind
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        
        return cleaned_text
