# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
import nltk

# nltk.download('punkt')
from nltk.tokenize import sent_tokenize

nltk.data.path.append("/tmp")
nltk.download("punkt", download_dir="/tmp")


class TextExtractionHandler:
    def handle(self, content, key, split_params):

        return self.intelligent_split(text, split_params)

    def intelligent_split(self, text, split_params):
        # Normalize whitespace once at the start.
        text = re.sub(r"\s+", " ", text.strip())

        # Sentences will not need whitespace normalization.
        sentences = sent_tokenize(text)

        chunks = []
        current_chunk = []
        current_chunk_size = 0
        char_index = 0
        content_index = 0
        min_chunk_size = split_params.get("min_chunk_size", 512)

        for sentence in sentences:
            sentence_length = len(sentence)
            # Check if adding this sentence would exceed the chunk size.
            if (
                current_chunk
                and (current_chunk_size + sentence_length + 1) >= min_chunk_size
            ):
                # Join the current chunk with space and create the chunk object.
                chunk_text = " ".join(current_chunk)
                chunk_location = {"nchar_index": char_index}

                chunks.append(
                    {
                        "content": chunk_text,
                        "location": chunk_location,
                        "content_index": content_index,
                    }
                )

                # Update char_index and reset current_chunk.
                char_index += (
                    len(chunk_text) + 1
                )  # Include the space that joins with the next chunk.
                current_chunk = [
                    sentence
                ]  # Start the new chunk with the current sentence.
                current_chunk_size = sentence_length
                content_index += 1  # Increment the content index.
            else:
                # If this is the first sentence, don't add a space at the start.
                if current_chunk:
                    current_chunk.append(sentence)
                    current_chunk_size += sentence_length + 1
                else:
                    current_chunk = [sentence]
                    current_chunk_size = sentence_length

        # If there's remaining text in the current chunk, add it as the last chunk.
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunk_location = {"nchar_index": char_index}

            chunks.append(
                {
                    "content": chunk_text,
                    "location": chunk_location,
                    "content_index": content_index,
                }
            )

        return chunks


# Example subclass for TXT files
class TextHandler(TextExtractionHandler):
    def extract_text(self, file_content):
        return file_content.decode("utf-8")
