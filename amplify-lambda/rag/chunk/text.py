# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
import nltk
from nltk.tokenize import sent_tokenize

# Global flag to track NLTK data initialization
_nltk_data_initialized = False

def _ensure_nltk_data():
    """
    Ensure NLTK punkt_tab data is available in Lambda environment.
    Downloads to /tmp on first call and reuses on subsequent invocations.

    CRITICAL: Must download to /tmp (not /tmp/nltk_data) because Lambda environment
    already searches /tmp in the default NLTK data path.
    """
    global _nltk_data_initialized

    if _nltk_data_initialized:
        return

    try:
        # Lambda's /tmp is writeable and already in NLTK's search path
        # We download directly to /tmp (not /tmp/nltk_data)
        tmp_path = "/tmp"

        # Ensure /tmp is in the NLTK data path (it usually is by default in Lambda)
        if tmp_path not in nltk.data.path:
            nltk.data.path.insert(0, tmp_path)

        # Try to use the tokenizer - will raise LookupError if not available
        try:
            sent_tokenize("Test sentence.")
            _nltk_data_initialized = True
            return
        except LookupError:
            pass

        # Download punkt_tab directly to /tmp
        # This creates /tmp/tokenizers/punkt_tab/... which NLTK will find
        download_result = nltk.download("punkt_tab", download_dir=tmp_path, quiet=False)

        if not download_result:
            raise RuntimeError("nltk.download returned False - download may have failed")

        # Verify download succeeded by actually using the tokenizer
        sent_tokenize("Test sentence.")
        _nltk_data_initialized = True

    except Exception as e:
        # Re-raise to fail fast - we can't proceed without tokenization
        raise RuntimeError(f"Cannot initialize NLTK tokenizer: {e}") from e


class TextExtractionHandler:
    def handle(self, content, key, split_params):
        return self.intelligent_split(content, split_params)

    def intelligent_split(self, text, split_params):
        # Initialize NLTK data for tokenization
        _ensure_nltk_data()

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
