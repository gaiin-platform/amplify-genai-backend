from markitdown import MarkItDown
import os
import tempfile
import re

# from markdown import MarkDownHandler


class MarkItDownExtractor:
    def __init__(self, docintel_endpoint=None):
        """Initialize the MarkItDown extractor with an optional document intelligence endpoint."""
        endpoint = docintel_endpoint or os.environ.get(
            "DOCINTEL_ENDPOINT", "<document_intelligence_endpoint>"
        )
        self.md = MarkItDown(docintel_endpoint=endpoint)

    def extract_from_path(self, file_path):
        """Extract text from a file on disk using MarkItDown."""
        try:
            result = self.md.convert(file_path)

            return result.text_content.replace("NaN", " ")
        except Exception as e:
            print(f"Error extracting text with MarkItDown from {file_path}: {str(e)}")
            return None

    def extract_from_content(self, file_content, file_name):
        """Extract text from file content bytes using MarkItDown."""
        try:
            # Create a temporary file to store the content
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=os.path.splitext(file_name)[1]
            ) as temp_file:
                temp_file.write(file_content)
                temp_path = temp_file.name

            # Process the temporary file
            result = self.md.convert(temp_path)

            # Clean up the temporary file
            os.unlink(temp_path)

            # Return the structured content
            return result.text_content.replace("NaN", " ")
        except Exception as e:
            print(f"Error extracting text with MarkItDown from content: {str(e)}")
            return None

    def _clean_excel_content(self, content):
        print(f"Cleaning Excel content")
        """Clean up the Excel content to extract meaningful data."""
        try:
            # Extract only the worksheet content that contains actual data
            worksheet_data = ""

            # Find the shared strings section to map string references
            shared_strings = {}
            shared_strings_match = re.search(
                r"## File: xl/sharedStrings\.xml.*?<sst.*?>(.*?)</sst>",
                content,
                re.DOTALL,
            )
            if shared_strings_match:
                string_entries = re.findall(
                    r"<si><t>(.*?)</t></si>", shared_strings_match.group(1)
                )
                for i, text in enumerate(string_entries):
                    shared_strings[str(i)] = text

            # Find worksheet data and extract cell values
            worksheet_match = re.search(
                r"## File: xl/worksheets/sheet\d+\.xml.*?<sheetData>(.*?)</sheetData>",
                content,
                re.DOTALL,
            )
            if worksheet_match:
                rows = re.findall(r"<row[^>]*>(.*?)</row>", worksheet_match.group(1))
                for row_idx, row in enumerate(rows):
                    row_data = []
                    cells = re.findall(
                        r'<c r="([^"]*)"(.*?)(?:><v>(.*?)</v></c>|/></c>)', row
                    )
                    for cell_ref, cell_attrs, cell_value in cells:
                        # Handle string type cells
                        if 't="s"' in cell_attrs and cell_value in shared_strings:
                            row_data.append(shared_strings[cell_value])
                        # Handle numeric and other types
                        elif cell_value:
                            row_data.append(cell_value)
                        else:
                            row_data.append("")

                    if row_data:
                        worksheet_data += " | ".join(row_data) + "\n"

            # If we managed to extract worksheet data, return it
            if worksheet_data:
                return f"Excel Worksheet Content:\n{worksheet_data}"

            # Otherwise return a cleaned version of the original content
            cleaned_content = re.sub(
                r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", "", content
            )
            cleaned_content = re.sub(r"<[^>]+>", " ", cleaned_content)
            cleaned_content = re.sub(r"\s+", " ", cleaned_content)
            return cleaned_content

        except Exception as e:
            print(f"Error cleaning Excel content: {str(e)}")
            # Fall back to the original content
            return content


# Example usage
if __name__ == "__main__":
    extractor = MarkItDownExtractor()
    file_path = "Test.pdf"

    # method 1 - Read from the file path:
    result = extractor.extract_from_path(file_path)
    if result:
        print(f"\n\nMethod 1 Result: \n\n{result}")

    # # method 2 - Read the file content:
    # with open(file_path, 'rb') as file:
    #     file_content = file.read()

    # result = extractor.extract_from_content(file_content, file_path)

    # # displays result
    # if result:
    #     print(f"\n\nMethod 2 Result: \n\n{result}")

    # md_bytes = result.encode('utf-8')

    # text = MarkDownHandler().extract_text(md_bytes, "file_name")
