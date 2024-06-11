
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import openpyxl
import io

from rag.handlers.text import TextExtractionHandler


def wrap_comma_with_quotes(s):
    if "," in s:
        s = '"' + s + '"'
    return s


class ExcelHandler(TextExtractionHandler):

    def extract_text(self, file_content, encoding):
        with io.BytesIO(file_content) as f:
            # Use openpyxl to load the workbook
            workbook = openpyxl.load_workbook(f, data_only=True)  # data_only=True to read values only, not formulas

            chunks = []
            for sheet_number, sheet in enumerate(workbook.sheetnames, start=1):
                current_sheet = workbook[sheet]

                for row_number, row in enumerate(current_sheet.iter_rows(values_only=True), start=1):
                    row_text = ",".join(wrap_comma_with_quotes(str(cell)) for cell in row if cell is not None)
                    row_text = row_text.strip()

                    chunks.append({
                                'content': row_text,
                                'tokens': self.num_tokens_from_string(row_text),
                                'location': {
                                    'sheet_number': sheet_number,
                                    'sheet_name': current_sheet.title,
                                    'row_number': row_number
                                },
                                'canSplit': False
                    })

            return chunks
