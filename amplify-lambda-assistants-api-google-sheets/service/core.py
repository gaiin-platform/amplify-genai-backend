from common.ops import vop
from common.validate import validated
from integrations.google.sheets import get_spreadsheet_rows, get_sheets_info, get_sheet_names, insert_rows, delete_rows, \
    update_rows, create_spreadsheet, apply_conditional_formatting, sort_range, find_replace, get_cell_formulas, \
    add_chart, apply_formatting, clear_range, rename_sheet, duplicate_sheet, execute_query
from integrations.oauth import MissingCredentialsError
import re

def camel_to_snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

def sheets_handler(operation, *required_params, **optional_params):
    def handler(event, context, current_user, name, data):
        try:
            params = {camel_to_snake(param): data['data'][param] for param in required_params}
            params.update({camel_to_snake(param): data['data'].get(param) for param in optional_params})
            response = operation(current_user, **params)
            return {"success": True, "data": response}
        except MissingCredentialsError as me:
            return {"success": False, "error": str(me)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return handler

@vop(
    path="/google/integrations/sheets/get-rows",
    tags=["default"],
    name="getSpreadsheetRows",
    description="Returns the rows from a Google Sheet as JSON.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "cellRange": "The range of cells to read as a string, such as A1:A"
    }
)
@validated("get_rows")
def get_sheet_rows(event, context, current_user, name, data):
    return sheets_handler(get_spreadsheet_rows, 'spreadsheetId', 'cellRange')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-info",
    tags=["default"],
    name="getGoogleSheetsInfo",
    description="Returns information about Google Sheets, including sheet names and sample data.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    }
)
@validated("get_google_sheets_info")
def get_google_sheets_info(event, context, current_user, name, data):
    return sheets_handler(get_sheets_info, 'spreadsheetId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-sheet-names",
    tags=["default"],
    name="getSheetNames",
    description="Returns the list of sheet names in a Google Sheets document.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    }
)
@validated("get_sheet_names")
def get_sheet_names_handler(event, context, current_user, name, data):
    return sheets_handler(get_sheet_names, 'spreadsheetId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/insert-rows",
    tags=["default"],
    name="insertRows",
    description="Inserts multiple new rows into a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to insert",
        "sheetName": "Optional: The name of the sheet to insert into",
        "insertionPoint": "Optional: The row number to start insertion at"
    }
)
@validated("insert_rows")
def insert_rows_handler(event, context, current_user, name, data):
    return sheets_handler(insert_rows, 'spreadsheetId', 'rowsData', sheetName=None, insertionPoint=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/delete-rows",
    tags=["default"],
    name="deleteRows",
    description="Deletes a range of rows from a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "startRow": "The first row to delete",
        "endRow": "The last row to delete (inclusive)",
        "sheetName": "Optional: The name of the sheet to delete from"
    }
)
@validated("delete_rows")
def delete_rows_handler(event, context, current_user, name, data):
    return sheets_handler(delete_rows, 'spreadsheetId', 'startRow', 'endRow', sheetName=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/update-rows",
    tags=["default"],
    name="updateRows",
    description="Updates specified rows in a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to update. The first item in each array should be the row number to update. Example to update rows 3 and 8: [[3, 'something'],[8, 'new value 1', 'new value 2']]",
        "sheetName": "Optional: The name of the sheet to update"
    }
)
@validated("update_rows")
def update_rows_handler(event, context, current_user, name, data):
    return sheets_handler(update_rows, 'spreadsheetId', 'rowsData', sheetName=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/create-spreadsheet",
    tags=["default"],
    name="createSpreadsheet",
    description="Creates a new Google Sheets spreadsheet.",
    params={
        "title": "The title of the new spreadsheet"
    }
)
@validated("create_spreadsheet")
def create_spreadsheet_handler(event, context, current_user, name, data):
    return sheets_handler(create_spreadsheet, 'title')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/duplicate-sheet",
    tags=["default"],
    name="duplicateSheet",
    description="Duplicates a sheet within a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to duplicate",
        "newSheetName": "The name for the new duplicated sheet"
    }
)
@validated("duplicate_sheet")
def duplicate_sheet_handler(event, context, current_user, name, data):
    return sheets_handler(duplicate_sheet, 'spreadsheetId', 'sheetId', 'newSheetName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/rename-sheet",
    tags=["default"],
    name="renameSheet",
    description="Renames a sheet in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to rename",
        "newName": "The new name for the sheet"
    }
)
@validated("rename_sheet")
def rename_sheet_handler(event, context, current_user, name, data):
    return sheets_handler(rename_sheet, 'spreadsheetId', 'sheetId', 'newName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/clear-range",
    tags=["default"],
    name="clearRange",
    description="Clears a range of cells in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to clear"
    }
)
@validated("clear_range")
def clear_range_handler(event, context, current_user, name, data):
    return sheets_handler(clear_range, 'spreadsheetId', 'rangeName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/apply-formatting",
    tags=["default"],
    name="applyFormatting",
    description="Applies formatting to a range of cells in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "startRow": "The starting row (1-indexed)",
        "endRow": "The ending row (1-indexed)",
        "startCol": "The starting column (1-indexed)",
        "endCol": "The ending column (1-indexed)",
        "formatJson": "The formatting to apply as a JSON object"
    }
)
@validated("apply_formatting")
def apply_formatting_handler(event, context, current_user, name, data):
    return sheets_handler(apply_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'formatJson')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/add-chart",
    tags=["default"],
    name="addChart",
    description="Adds a chart to a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "chartSpec": "The chart specification as a JSON object"
    }
)
@validated("add_chart")
def add_chart_handler(event, context, current_user, name, data):
    return sheets_handler(add_chart, 'spreadsheetId', 'sheetId', 'chartSpec')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-cell-formulas",
    tags=["default"],
    name="getCellFormulas",
    description="Gets cell formulas for a range in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to get formulas from"
    }
)
@validated("get_cell_formulas")
def get_cell_formulas_handler(event, context, current_user, name, data):
    return sheets_handler(get_cell_formulas, 'spreadsheetId', 'rangeName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/find-replace",
    tags=["default"],
    name="findReplace",
    description="Finds and replaces text in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "find": "The text to find",
        "replace": "The text to replace with",
        "sheetId": "Optional: The ID of the sheet to perform find/replace on"
    }
)
@validated("find_replace")
def find_replace_handler(event, context, current_user, name, data):
    return sheets_handler(find_replace, 'spreadsheetId', 'find', 'replace', sheetId='sheetId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/sort-range",
    tags=["default"],
    name="sortRange",
    description="Sorts a range of data in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "startRow": "The starting row (1-indexed)",
        "endRow": "The ending row (1-indexed)",
        "startCol": "The starting column (1-indexed)",
        "endCol": "The ending column (1-indexed)",
        "sortOrder": "The sort order specification as a JSON object"
    }
)
@validated("sort_range")
def sort_range_handler(event, context, current_user, name, data):
    return sheets_handler(sort_range, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'sortOrder')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/apply-conditional-formatting",
    tags=["default"],
    name="applyConditionalFormatting",
    description="Applies conditional formatting to a range in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "startRow": "The starting row (1-indexed)",
        "endRow": "The ending row (1-indexed)",
        "startCol": "The starting column (1-indexed)",
        "endCol": "The ending column (1-indexed)",
        "condition": "The condition for the formatting as a JSON object",
        "format": "The format to apply as a JSON object"
    }
)
@validated("apply_conditional_formatting")
def apply_conditional_formatting_handler(event, context, current_user, name, data):
    return sheets_handler(apply_conditional_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'condition', 'format')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/sheets/execute-query",
    tags=["default"],
    name="executeQuery",
    description="Executes a SQL-like query on a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "query": "The SQL-like query to execute e.g., a == 1 and b < 34",
        "sheetName": "(Optional) The name of the sheet to query"
    }
)
@validated("execute_query")
def execute_query_handler(event, context, current_user, name, data):
    return sheets_handler(execute_query, 'spreadsheetId', 'query', sheetName='sheetName')(event, context, current_user, name, data)
