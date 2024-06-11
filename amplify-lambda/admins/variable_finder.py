
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import re
import sys

def find_and_print_text_blocks(text):
    # Regular expression pattern for '{{...}}', including newlines
    pattern = re.compile(r'{{(.*?)}}', re.DOTALL)
    matches = pattern.findall(text)

    for match in matches:
        # Remove internal line breaks and print
        cleaned_match = match.replace('\n', ' ')
        print(cleaned_match)

if __name__ == "__main__":
    # Check if a filename was provided
    if len(sys.argv) < 2:
        print("Usage: python script.py <filename>")
        sys.exit(1)

    # Read text from the file specified by the command line parameter
    filename = sys.argv[1]
    try:
        with open(filename, 'r') as file:
            text = file.read()
        # Extract and print all text blocks
        find_and_print_text_blocks(text)
    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")