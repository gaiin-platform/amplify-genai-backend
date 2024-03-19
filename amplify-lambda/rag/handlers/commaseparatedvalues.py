import csv
import io
import chardet

from rag.handlers.text import TextExtractionHandler

def is_likely_text(file_content):
    # Use chardet to detect the encoding of the file_content
    result = chardet.detect(file_content)
    confidence = result['confidence']  # How confident chardet is about its detection
    encoding = result['encoding']
    is_text = result['encoding'] is not None and confidence > 0.7  # You can adjust the confidence threshold

    return is_text, encoding


def wrap_comma_with_quotes(s):
    if "," in s:
        s = '"' + s + '"'
    return s


class CSVHandler(TextExtractionHandler):

    def extract_text(self, file_content, key):
        is_text, encoding = is_likely_text(file_content)

        with io.BytesIO(file_content) as f:
            # Decode the file content into a string and use csv.reader to read it
            reader = csv.reader(io.StringIO(f.read().decode(encoding)))
            rows = list(reader)  # Convert the reader object to a list of rows for reusability

            chunks = []
            current_chunk_content = ''
            current_chunk_location = None

            for row_number, row in enumerate(rows, start=1):
                row_text = ",".join(wrap_comma_with_quotes(str(value)) for value in row if value)
                row_text = row_text.strip()

                if row_text:  # If there is existing text, include it as a new chunk
                    chunks.append({
                        'content': row_text,
                        'tokens': self.num_tokens_from_string(row_text),
                        'location': {'row_number': row_number},
                        'canSplit': False
                    })

            return chunks
