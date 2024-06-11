
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
import chardet
import io
import tiktoken


def is_likely_text(file_content):
    # Use chardet to detect the encoding of the file_content
    result = chardet.detect(file_content)
    confidence = result['confidence']  # How confident chardet is about its detection
    encoding = result['encoding']
    is_text = result['encoding'] is not None and confidence > 0.7  # You can adjust the confidence threshold

    return is_text, encoding
class TextExtractionHandler:
    def __init__(self):
        self.enc = tiktoken.get_encoding("cl100k_base")
    def num_tokens_from_string(self, string: str) -> int:
        """Returns the number of tokens in a text string."""
        num_tokens = len(self.enc.encode(string))
        return num_tokens

    def extract_text(self, file_content, key):
        is_text, encoding = is_likely_text(file_content)

        chunks = []

        with io.BytesIO(file_content) as f:
            # Wrap the byte stream with io.TextIOWrapper to handle text encoding
            text_stream = io.TextIOWrapper(f, encoding=encoding)

            # Now you can iterate over the lines
            for line_num, line in enumerate(text_stream, start=1):
                chunks.append({
                    'content': line,
                    'tokens': self.num_tokens_from_string(line),
                    'location': {
                        'line_number': line_num
                    },
                    'canSplit': True
                })
            return chunks




# Example subclass for TXT files
class TextHandler(TextExtractionHandler):
    def extract_text(self, file_content, key):
        return super().extract_text(file_content, key)