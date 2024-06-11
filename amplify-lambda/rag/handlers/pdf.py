
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import io
import pypdfium2 as pdfium

from rag.handlers.text import TextExtractionHandler


class PDFHandler(TextExtractionHandler):
    def extract_text(self, file_content, encoding):
        with io.BytesIO(file_content) as f:
            pdf = pdfium.PdfDocument(f)

            chunks = []
            num_pages = len(pdf)  # Get the number of pages in the document

            for page_index in range(num_pages):
                page_number = page_index + 1  # Convert zero-based index to one-based page numbering for display

                page = pdf[page_index]  # Load the page using zero-based indexing
                textpage = page.get_textpage()

                # Extract text from the whole page
                text = textpage.get_text_range()

                if not text:
                    continue

                chunk = {
                        'content': text,
                        'tokens': self.num_tokens_from_string(text),
                        'location': {'page_number': page_number},
                        'canSplit': True
                }
                chunks.append(chunk)

                # pypdfium2 might not require explicit page close, depending on API implementation details
                # If needed: page.close()

            # Since pypdfium2 loads the whole document, ensure to close it to free resources
            pdf.close()

            return chunks
