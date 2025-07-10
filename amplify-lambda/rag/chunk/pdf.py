# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

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
                page_number = (
                    page_index + 1
                )  # Convert zero-based index to one-based page numbering for display

                page = pdf[page_index]  # Load the page using zero-based indexing
                textpage = page.get_textpage()

                # Extract text from the whole page
                text = textpage.get_text_range()

                if not text:
                    continue

                chunk = {
                    "content": text,
                    "location": {"page": page_number},
                    "canSplit": True,
                }
                chunks.append(chunk)

                # pypdfium2 might not require explicit page close, depending on API implementation details
                # If needed: page.close()

            # Since pypdfium2 loads the whole document, ensure to close it to free resources
            pdf.close()

            return chunks

    def handle(self, file_content, key, split_params):
        with io.BytesIO(file_content) as f:
            pdf = pdfium.PdfDocument(f)

            min_chunk_size = split_params.get("min_chunk_size", 100)

            chunks = []
            num_pages = len(pdf)  # Get the number of pages in the document

            for page_index in range(num_pages):
                page_number = (
                    page_index + 1
                )  # Convert zero-based index to one-based page numbering for display

                page = pdf[page_index]  # Load the page using zero-based indexing
                textpage = page.get_textpage()

                # Extract text from the whole page
                text = textpage.get_text_range()

                if not text:
                    continue

                # Use the whole page text if long enough or split into smaller chunks if configured
                if len(text) > min_chunk_size:
                    chunks.extend(self.intelligent_split(text, split_params))
                else:
                    chunk = {"content": text, "location": {"page": page_number}}
                    chunks.append(chunk)

                # pypdfium2 might not require explicit page close, depending on API implementation details
                # If needed: page.close()

            # Since pypdfium2 loads the whole document, ensure to close it to free resources
            pdf.close()

            # Add page information to each chunk
            for chunk in chunks:
                chunk["location"] = {
                    "page": page_index + 1
                }  # Update this according to how you wish to handle the page info

            return chunks

    # def handle(self, file_content, key, split_params):
    #     min_chunk_size = split_params.get('min_chunk_size', 100)
    #
    #     chunks = []
    #     with fitz.open(stream=file_content, filetype="pdf") as pdf:
    #         for page_number in range(pdf.page_count):
    #             page = pdf[page_number]
    #             text = page.get_text()
    #
    #             if not text:
    #                 continue
    #
    #             # Use the whole page text if long enough or split into smaller chunks if configured
    #             if len(text) > min_chunk_size:
    #                 chunks.extend(self.intelligent_split(text, split_params))
    #
    #             else:
    #                 chunk = {
    #                     'content': text,
    #                     'location': {'page': page_number + 1}
    #                 }
    #                 chunks.append(chunk)
    #
    #     # Correct the page information for each chunk
    #     for chunk in chunks:
    #         # Assuming that 'page_number' value should be unique for each chunk
    #         chunk['location']['page'] = chunk.get('content_index', page_number + 1)
    #
    #     return chunks
