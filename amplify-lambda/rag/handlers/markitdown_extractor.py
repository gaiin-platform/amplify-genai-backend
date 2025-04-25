from markitdown import MarkItDown
import os
import tempfile

class MarkItDownExtractor:
    def __init__(self, docintel_endpoint=None):
        """Initialize the MarkItDown extractor with an optional document intelligence endpoint."""
        endpoint = docintel_endpoint or os.environ.get("DOCINTEL_ENDPOINT", "<document_intelligence_endpoint>")
        self.md = MarkItDown(docintel_endpoint=endpoint)
    
    def extract_from_path(self, file_path):
        """Extract text from a file on disk using MarkItDown."""
        try:
            result = self.md.convert(file_path)
            # Extract text content and structure
            return {
                'content': self._parse_content(result),
                'text': result.text_content
            }
        except Exception as e:
            print(f"Error extracting text with MarkItDown from {file_path}: {str(e)}")
            return None
    
    def extract_from_content(self, file_content, file_name):
        """Extract text from file content bytes using MarkItDown."""
        try:
            # Create a temporary file to store the content
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
                temp_file.write(file_content)
                temp_path = temp_file.name
            
            # Process the temporary file
            result = self.md.convert(temp_path)
            
            # Clean up the temporary file
            os.unlink(temp_path)
            
            # Return the structured content
            return {
                'content': self._parse_content(result),
                'text': result.text_content
            }
        except Exception as e:
            print(f"Error extracting text with MarkItDown from content: {str(e)}")
            return None
    
    def _parse_content(self, result):
        """Parse the MarkItDown result into a structured format compatible with our system."""
        # This would need to be adapted based on the actual structure returned by MarkItDown
        # For now, creating a basic structure similar to our existing extraction
        structured_content = []
        
        # Basic implementation - extract paragraphs or sections
        if hasattr(result, 'sections') and result.sections:
            for i, section in enumerate(result.sections):
                structured_content.append({
                    'content': section.text,
                    'canSplit': True,
                    'location': {
                        'page': section.page if hasattr(section, 'page') else 1,
                        'section': i
                    }
                })
        elif result.text_content:
            # If no sections, just use the whole text content
            structured_content.append({
                'content': result.text_content,
                'canSplit': True,
                'location': {'page': 1}
            })
            
        return structured_content

# Example usage
if __name__ == "__main__":
    extractor = MarkItDownExtractor()
    result = extractor.extract_from_path("test.pdf")
    if result:
        print(result['text'])