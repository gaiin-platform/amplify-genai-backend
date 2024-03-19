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

            for row_number, row in enumerate(rows):
                row_text = " ".join(str(value) for value in row if value)
                row_text = row_text.strip()

                if row_text:  # If there is existing text, include it as a new chunk
                    chunks.append({
                        'content': row_text,
                        'location': {'row_number': row_number},
                        'canSplit': False
                    })

            return chunks

    def handle(self, file_content, key, split_params):
        rows = self.extract_text(file_content, key)
        min_chunk_size = split_params.get('min_chunk_size', 100)

        chunks = []
        current_chunk_content = ''
        current_chunk_location = None

        for row_number, row in enumerate(rows, start=1):
            row_text = " ".join(str(value) for value in row if value)
            row_text = row_text.strip()

            if row_text and (len(current_chunk_content) + len(row_text)) >= min_chunk_size:
                if current_chunk_content:  # If there is existing text, include it as a new chunk
                    chunks.append({
                        'content': current_chunk_content,
                        'location': {'row_number': current_chunk_location}
                    })
                    current_chunk_content = ''
                current_chunk_location = row_number

            current_chunk_content += ' ' + row_text if current_chunk_content else row_text

        # If there is remaining text after the loop, add it as the last chunk
        if current_chunk_content:
            chunks.append({
                'content': current_chunk_content,
                'location': {'row_number': current_chunk_location}
            })

        return chunks