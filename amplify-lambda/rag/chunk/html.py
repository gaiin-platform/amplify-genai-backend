# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from bs4 import BeautifulSoup
from rag.handlers.text import TextExtractionHandler


class HTMLHandler(TextExtractionHandler):
    def extract_text(self, file_content, key):
        # Parse the HTML content with BeautifulSoup
        soup = BeautifulSoup(file_content, "html.parser")
        # Get the text content, stripping all the tags
        text = soup.get_text(separator=" ", strip=True)
        return text

    def handle(self, file_content, key, split_params):
        text = self.extract_text(file_content)

        # Use intelligent_split to split the text into chunks based on the min_chunk_size
        min_chunk_size = split_params.get("min_chunk_size", 100)
        split_pattern = split_params.get("split_pattern", r"(?<=[.?!])\s+")

        # Further split the text if necessary and yield chunks
        chunks = self.intelligent_split(text, split_params)

        # Since HTML doesn't have explicit page numbers, we omit location details or we could add placeholders
        for chunk in chunks:
            chunk["location"] = {
                "page": 1
            }  # Adding a default page number since HTML has no pages

        return chunks
