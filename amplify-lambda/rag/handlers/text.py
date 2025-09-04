# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import io
import tiktoken
from rag.handlers.shared_functions import is_likely_text


class TextExtractionHandler:
    def __init__(self):
        self.enc = tiktoken.get_encoding("cl100k_base")

    def num_tokens_from_string(self, string: str) -> int:
        """Returns the number of tokens in a text string."""
        num_tokens = len(self.enc.encode(string))
        return num_tokens

    def extract_text(self, file_content, visual_map={}):
        is_text, encoding = is_likely_text(file_content)

        chunks = []

        with io.BytesIO(file_content) as f:
            # Wrap the byte stream with io.TextIOWrapper to handle text encoding
            text_stream = io.TextIOWrapper(f, encoding=encoding)

            # Now you can iterate over the lines
            for line_num, line in enumerate(text_stream, start=1):
                chunks.append(
                    {
                        "content": line,
                        "tokens": self.num_tokens_from_string(line),
                        "location": {"line_number": line_num},
                        "canSplit": True,
                    }
                )
            return chunks


# Example subclass for TXT files
class TextHandler(TextExtractionHandler):
    def extract_text(self, file_content, visual_map={}):
        return super().extract_text(file_content, visual_map)
