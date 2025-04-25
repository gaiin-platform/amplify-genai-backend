#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
from rag.handlers.text import TextExtractionHandler

class MarkDownHandler(TextExtractionHandler):
    """
    Handler for processing Markdown content, removing unnecessary markdown syntax
    while preserving the semantic structure and meaning of the content.
    """

    def extract_text(self, file_content, file_name):
        """
        Extract text from Markdown content, cleaning markdown syntax as appropriate.
        
        Args:
            file_content: The binary content of the file
            file_name: The name of the file
            
        Returns:
            A list of dicts containing the cleaned content with structure information
        """
        try:
            # Decode the markdown content to string
            if isinstance(file_content, bytes):
                text = file_content.decode('utf-8')
            else:
                text = file_content
                
            # Apply markdown cleaning operations
            cleaned_text = self._clean_markdown(text)

            print(f"Extract md Cleaned text: {cleaned_text}")
            
            # Split into paragraphs (blank line separation)
            paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned_text) if p.strip()]
            
            chunks = []
            line_number = 1
            
            for i, paragraph in enumerate(paragraphs):
                # Skip empty paragraphs
                if not paragraph.strip():
                    continue
                
                # Calculate tokens
                tokens = self.num_tokens_from_string(paragraph)
                
                # Create the chunk
                chunk = {
                    'content': paragraph,
                    'tokens': tokens,
                    'location': {
                        'paragraph_number': i + 1,
                        'line_number': line_number
                    },
                    'canSplit': True
                }
                
                # Update line number (approximation for visual position in document)
                line_number += paragraph.count('\n') + 2  # +2 for the paragraph break
                
                chunks.append(chunk)
            
            return chunks
            
        except Exception as e:
            print(f"Error processing markdown file {file_name}: {str(e)}")
            return []
    
    def _clean_markdown(self, text):
        """
        Clean markdown syntax while preserving semantic meaning.
        
        Args:
            text: Original markdown text
            
        Returns:
            Cleaned text with appropriate markdown elements removed/transformed
        """
        # Keep track of the original text for reference
        original_text = text
        
        # HEADERS: Convert headers to plain text but preserve their prominence
        # Replace ATX-style headers (# Header)
        text = re.sub(r'^#{1,6}\s+(.*?)$', r'\1', text, flags=re.MULTILINE)
        
        # Replace Setext-style headers (Header\n======)
        text = re.sub(r'^(.*?)\n[=]{2,}$', r'\1', text, flags=re.MULTILINE)
        text = re.sub(r'^(.*?)\n[-]{2,}$', r'\1', text, flags=re.MULTILINE)
        
        # EMPHASIS: Remove emphasis markers but keep the content
        # Bold: **text** or __text__
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'__(.*?)__', r'\1', text)
        
        # Italic: *text* or _text_
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        text = re.sub(r'_(.*?)_', r'\1', text)
        
        # Bold-italic: ***text*** or ___text___
        text = re.sub(r'\*\*\*(.*?)\*\*\*', r'\1', text)
        text = re.sub(r'___(.*?)___', r'\1', text)
        
        # LISTS: Preserve list structure
        # Unordered lists: -, +, * - Convert to bullet points
        text = re.sub(r'^\s*[-+*]\s+(.*?)$', r'• \1', text, flags=re.MULTILINE)
        
        # Ordered lists: 1., 2., etc. - Preserve the numbers
        # Now we preserve the number:
        text = re.sub(r'^\s*(\d+)\.\s+(.*?)$', r'\1. \2', text, flags=re.MULTILINE)
        
        # CODE: Keep code content without the backticks
        # Inline code: `code`
        text = re.sub(r'`(.*?)`', r'\1', text)
        
        # Code blocks: ```language\ncode\n```
        text = re.sub(r'```[\w]*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)
        
        # Indented code blocks
        text = re.sub(r'^( {4}|\t)(.*?)$', r'\2', text, flags=re.MULTILINE)
        
        # LINKS: Keep the link text and add the URL in parentheses if it adds context
        # [text](url) -> text (url)
        text = re.sub(r'\[(.*?)\]\((.*?)\)', lambda m: f"{m.group(1)} ({m.group(2)})" 
                     if m.group(1).lower() != m.group(2).lower() else m.group(1), text)
        
        # IMAGES: Convert to text description
        # ![alt text](url) -> [Image: alt text]
        text = re.sub(r'!\[(.*?)\]\(.*?\)', r'[Image: \1]', text)
        
        # BLOCKQUOTES: Remove '>' but keep the content
        # > quote -> quote
        text = re.sub(r'^\s*>\s+(.*?)$', r'\1', text, flags=re.MULTILINE)
        
        # HORIZONTAL RULES: Replace with a line break
        # ---, ***, ___ -> (empty line)
        text = re.sub(r'^\s*([*\-_])\s*(\1\s*){2,}$', r'', text, flags=re.MULTILINE)
        
        # TABLES: Optimize table structure for LLM processing

        # Check if there are tables in the text (look for typical markdown table patterns)
        if re.search(r'^\s*\|.*\|\s*$', text, flags=re.MULTILINE) or re.search(r'^\s*\|[-:|]+\|', text, flags=re.MULTILINE):
            # Process the markdown tables into a more structured format
            lines = text.split('\n')
            new_lines = []
            in_table = False
            header_processed = False
            header_row = []
            current_table = []
            
            for line in lines:
                # Detect table rows (lines with multiple | characters)
                if re.match(r'^\s*\|.*\|\s*$', line) and '|' in line[1:]:
                    if not in_table:
                        in_table = True
                        new_lines.append("TABLE:")  # Clearly mark the start of a table
                    
                    # Skip separator rows (e.g., |---|---|)
                    if re.match(r'^\s*\|?\s*[-:]+[-| :]+\s*\|?\s*$', line):
                        continue
                    
                    # Process cells
                    cells = []
                    # Split by pipe character and remove leading/trailing pipes
                    row_cells = line.strip().split('|')
                    if row_cells[0].strip() == '':
                        row_cells = row_cells[1:]
                    if row_cells and row_cells[-1].strip() == '':
                        row_cells = row_cells[:-1]
                    
                    # Clean cell content
                    cells = [cell.strip() for cell in row_cells]
                    
                    # If this is the first row and we haven't processed a header yet, 
                    # treat it as the header row
                    if not header_processed:
                        header_row = cells
                        new_lines.append("HEADER: " + " | ".join(header_row))
                        header_processed = True
                        continue
                    
                    # For data rows, create structured key-value pairs for each cell
                    row_data = []
                    for i, cell in enumerate(cells):
                        if i < len(header_row):
                            column_name = header_row[i]
                            if column_name and cell:  # Only add if both column name and cell value exist
                                row_data.append(f"{column_name}: {cell}")
                    
                    # Format the row output
                    if row_data:
                        new_lines.append("ROW: " + "; ".join(row_data))
                    
                    current_table.append(cells)
                else:
                    # When we exit a table, add a marker
                    if in_table:
                        in_table = False
                        header_processed = False
                        current_table = []
                        new_lines.append("END_TABLE")
                    
                    # Add non-table line
                    new_lines.append(line)
            
            # If we were still in a table at the end, close it
            if in_table:
                new_lines.append("END_TABLE")
            
            # Replace the text with our processed version
            text = '\n'.join(new_lines)
        else:
            # If there are no tables, just use the more basic table cleanup
            # Remove leading/trailing |
            text = re.sub(r'^\s*\|\s*(.*?)\s*\|\s*$', r'\1', text, flags=re.MULTILINE)
            
            # Remove table separators (rows with |----|----| pattern)
            text = re.sub(r'^\s*\|?\s*[-:]+[-| :]+\s*\|?\s*$', r'', text, flags=re.MULTILINE)
            
            # Clean up remaining table cells
            text = re.sub(r'\|\s*', r' | ', text)
        
        # ESCAPE CHARACTERS: Replace escaped characters with their literal form
        # \*, \_, etc. -> *, _, etc.
        text = re.sub(r'\\([\\`*_{}[\]()#+\-.!|])', r'\1', text)
        
        # HTML TAGS: Remove simple HTML tags
        text = re.sub(r'</?[a-z][a-z0-9]*[^<>]*>', r'', text, flags=re.IGNORECASE)
        
        # Handle HTML comments, with special treatment for slide numbers
        text = re.sub(r'<!--\s*Slide\s+number:\s*(\d+)\s*-->', r'[Slide \1]', text, flags=re.IGNORECASE)
        # Remove any other HTML comments
        text = re.sub(r'<!--.*?-->', r'', text, flags=re.DOTALL)
        
        # Clean up excessive whitespace
        # Replace multiple spaces with a single space
        text = re.sub(r' {2,}', ' ', text)
        
        # Normalize line breaks
        text = re.sub(r'\r\n|\r', '\n', text)
        
        # Remove excessive line breaks (more than 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Final trim
        text = text.strip()
        
        return text
