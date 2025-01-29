from common.ops import vop
from common.validate import validated
from integrations.google.forms import create_form, get_form_details, add_question, update_question, delete_question, \
    get_responses, get_response, set_form_settings, get_form_link, update_form_info, list_user_forms
from integrations.google.calendar import get_events_between_dates, create_event, check_event_conflicts, \
    get_free_time_slots, get_events_for_date, get_event_details, delete_event, update_event
from integrations.google.docs import find_text_indices, append_text
from integrations.google.drive import convert_file, share_file, create_shared_link, get_download_link, create_file, \
    get_file_content, get_file_metadata, search_files, list_files, list_folders, move_item, copy_item, rename_item, \
    delete_item_permanently, get_file_revisions, create_folder, get_root_folder_ids
from integrations.google.gmail import get_messages_details, compose_and_send_email, compose_email_draft, get_recent_messages, search_messages, \
    get_attachment_links, get_attachment_content, create_filter, create_label, create_auto_filter_label_rule, \
    get_messages_from_date
from integrations.google.people import remove_contacts_from_group, add_contacts_to_group, delete_contact_group, \
    update_contact_group, create_contact_group, list_contact_groups, delete_contact, update_contact, create_contact, \
    get_contact_details, search_contacts
from integrations.google.sheets import get_spreadsheet_rows, get_sheets_info, get_sheet_names, insert_rows, delete_rows, \
    update_rows, create_spreadsheet, apply_conditional_formatting, sort_range, find_replace, get_cell_formulas, \
    add_chart, apply_formatting, clear_range, rename_sheet, duplicate_sheet, execute_query
from integrations.google.docs import create_new_document, get_document_contents, insert_text, replace_text, create_document_outline, export_document, share_document, find_text_indices
from integrations.oauth import MissingCredentialsError
import re

def camel_to_snake(name):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
    return snake

def common_handler(operation, *required_params, **optional_params):
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
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet as a string"
            },
            "cellRange": {
                "type": "string",
                "description": "The range of cells to read as a string, such as A1:A"
            }
        },
        "required": ["spreadsheetId", "cellRange"]
    }
)
@validated("get_rows")
def get_sheet_rows(event, context, current_user, name, data):
    return common_handler(get_spreadsheet_rows, 'spreadsheetId', 'cellRange')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-info",
    tags=["default"],
    name="getGoogleSheetsInfo",
    description="Returns information about Google Sheets, including sheet names and sample data.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet as a string"
            }
        },
        "required": ["spreadsheetId"]
    }
)
@validated("get_google_sheets_info")
def get_google_sheets_info(event, context, current_user, name, data):
    return common_handler(get_sheets_info, 'spreadsheetId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-sheet-names",
    tags=["default"],
    name="getSheetNames",
    description="Returns the list of sheet names in a Google Sheets document.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet as a string"
            }
        },
        "required": ["spreadsheetId"]
    }
)
@validated("get_sheet_names")
def get_sheet_names_handler(event, context, current_user, name, data):
    return common_handler(get_sheet_names, 'spreadsheetId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/insert-rows",
    tags=["default"],
    name="insertRows",
    description="Inserts multiple new rows into a Google Sheet.",
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "rowsData": {
                "type": "array",
                "items": {
                    "type": "array"
                },
                "description": "An array of arrays, each representing a row to insert"
            },
            "sheetName": {
                "type": "string",
                "description": "The name of the sheet to insert into (leave blank for first sheet)",
            },
            "insertionPoint": {
                "type": "integer",
                "description": "The row number to start insertion at",
                "default": 1  # Start at first row by default
            }
        },
        "required": ["spreadsheetId", "rowsData"]
    }
)
@validated("insert_rows")
def insert_rows_handler(event, context, current_user, name, data):
    return common_handler(insert_rows, 'spreadsheetId', 'rowsData', sheetName=None, insertionPoint=None)(event, context, current_user, name, data)

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
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet as a string"
            },
            "startRow": {
                "type": "integer",
                "description": "The first row to delete"
            },
            "endRow": {
                "type": "integer",
                "description": "The last row to delete (inclusive)"
            },
            "sheetName": {
                "type": ["string"],
                "description": "Optional: The name of the sheet to delete from (omit for first sheet)"
            }
        },
        "required": ["spreadsheetId", 'startRow', 'endRow']
    }
)
@validated("delete_rows")
def delete_rows_handler(event, context, current_user, name, data):
    return common_handler(delete_rows, 'spreadsheetId', 'startRow', 'endRow', sheetName=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/update-rows",
    tags=["default"],
    name="updateRows",
    description="Updates specified rows in a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to update. The first item in each array should be the row number to update. Example to update rows 3 and 8: [[3, 'something'],[8, 'new value 1', 'new value 2']]",
        "sheetName": "Optional: The name of the sheet to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet as a string"
            },
            "rowsData": {
                "type": "array",
                "items": {
                    "type": "array"
                },
                "description": "An array of arrays, each representing a row to update. The first item in each array should be the row number to update. Example to update rows 3 and 8: [[3, 'something'],[8, 'new value 1', 'new value 2']]"
            },
            "sheetName": {
                "type": ["string"],
                "description": "Optional: The name of the sheet to update (omit for first sheet)",
            }
        },
        "required": ["spreadsheetId", 'rowsData']
    }
)
@validated("update_rows")
def update_rows_handler(event, context, current_user, name, data):
    return common_handler(update_rows, 'spreadsheetId', 'rowsData', sheetName=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/create-spreadsheet",
    tags=["default"],
    name="createSpreadsheet",
    description="Creates a new Google Sheets spreadsheet.",
    params={
        "title": "The title of the new spreadsheet"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title of the new spreadsheet"
            }
        },
        "required": ["title"]
    }
)
@validated("create_spreadsheet")
def create_spreadsheet_handler(event, context, current_user, name, data):
    return common_handler(create_spreadsheet, 'title')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/duplicate-sheet",
    tags=["default"],
    name="duplicateSheet",
    description="Duplicates a sheet within a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to duplicate",
        "newSheetName": "The name for the new duplicated sheet"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet to duplicate"
            },
            "newSheetName": {
                "type": "string",
                "description": "The name for the new duplicated sheet"
            }
        },
        "required": ["spreadsheetId", 'sheetId', 'newSheetName']
    }
)
@validated("duplicate_sheet")
def duplicate_sheet_handler(event, context, current_user, name, data):
    return common_handler(duplicate_sheet, 'spreadsheetId', 'sheetId', 'newSheetName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/rename-sheet",
    tags=["default"],
    name="renameSheet",
    description="Renames a sheet in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to rename",
        "newName": "The new name for the sheet"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet to rename"
            },
            "newName": {
                "type": "string",
                "description": "The new name for the sheet"
            }
        },
        "required": ["spreadsheetId", 'sheetId', 'newName']
    }
)
@validated("rename_sheet")
def rename_sheet_handler(event, context, current_user, name, data):
    return common_handler(rename_sheet, 'spreadsheetId', 'sheetId', 'newName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/clear-range",
    tags=["default"],
    name="clearRange",
    description="Clears a range of cells in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to clear"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "rangeName": {
                "type": "string",
                "description": "The A1 notation of the range to clear"
            }
        },
        "required": ["spreadsheetId", 'rangeName']
    }
)
@validated("clear_range")
def clear_range_handler(event, context, current_user, name, data):
    return common_handler(clear_range, 'spreadsheetId', 'rangeName')(event, context, current_user, name, data)

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
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet"
            },
            "startRow": {
                "type": "integer",
                "description": "The starting row (1-indexed)"
            },
            "endRow": {
                "type": "integer",
                "description": "The ending row (1-indexed)"
            },
            "startCol": {
                "type": "integer",
                "description": "The starting column (1-indexed)"
            },
            "endCol": {
                "type": "integer",
                "description": "The ending column (1-indexed)"
            },
            'formatJson': {
                'type': 'object',
                'description': 'The formatting to apply as a JSON object'
            }
        },
        'required': ['spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'formatJson']
    }
)
@validated("apply_formatting")
def apply_formatting_handler(event, context, current_user, name, data):
    return common_handler(apply_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'formatJson')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/add-chart",
    tags=["default"],
    name="addChart",
    description="Adds a chart to a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "chartSpec": "The chart specification as a JSON object"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet"
            },
            'chartSpec': {
                'type': 'object',
                'description': 'The chart specification as a JSON object'
            }
        },
        'required': ['spreadsheetId', 'sheetId', 'chartSpec']
    }
)
@validated("add_chart")
def add_chart_handler(event, context, current_user, name, data):
    return common_handler(add_chart, 'spreadsheetId', 'sheetId', 'chartSpec')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/get-cell-formulas",
    tags=["default"],
    name="getCellFormulas",
    description="Gets cell formulas for a range in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to get formulas from"
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "rangeName": {
                "type": "string",
                "description": "The A1 notation of the range to get formulas from"
            }
        },
        "required": ["spreadsheetId", 'rangeName']
    }
)
@validated("get_cell_formulas")
def get_cell_formulas_handler(event, context, current_user, name, data):
    return common_handler(get_cell_formulas, 'spreadsheetId', 'rangeName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/sheets/find-replace",
    tags=["default"],
    name="findReplace",
    description="Finds and replaces text in a Google Sheets spreadsheet.",
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "find": {
                "type": "string",
                "description": "The text to find"
            },
            "replace": {
                "type": "string",
                "description": "The text to replace with"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet to perform find/replace on",
                "default": 0  # Default to first sheet
            }
        },
        "required": ["spreadsheetId", "find", "replace"]
    }
)
@validated("find_replace")
def find_replace_handler(event, context, current_user, name, data):
    return common_handler(find_replace, 'spreadsheetId', 'find', 'replace', sheetId='sheetId')(event, context, current_user, name, data)

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
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet"
            },
            "startRow": {
                "type": "integer",
                "description": "The starting row (1-indexed)"
            },
            "endRow": {
                "type": "integer",
                "description": "The ending row (1-indexed)"
            },
            "startCol": {
                "type": "integer",
                "description": "The starting column (1-indexed)"
            },
            "endCol": {
                "type": "integer",
                "description": "The ending column (1-indexed)"
            },
            'sortOrder': {
                'type': 'object',
                'description': 'The sort order specification as a JSON object'
            }
        },
        'required': ['spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'sortOrder']
    }
)
@validated("sort_range")
def sort_range_handler(event, context, current_user, name, data):
    return common_handler(sort_range, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'sortOrder')(event, context, current_user, name, data)

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
    },
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "sheetId": {
                "type": "integer",
                "description": "The ID of the sheet"
            },
            "startRow": {
                "type": "integer",
                "description": "The starting row (1-indexed)"
            },
            "endRow": {
                "type": "integer",
                "description": "The ending row (1-indexed)"
            },
            "startCol": {
                "type": "integer",
                "description": "The starting column (1-indexed)"
            },
            "endCol": {
                "type": "integer",
                "description": "The ending column (1-indexed)"
            },
            'condition': {
                'type': 'object',
                'description': 'The condition for the formatting as a JSON object'
            },
            'format': {
                'type': 'object',
                'description': 'The format to apply as a JSON object'
            }
        },
        'required': ['spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'condition', 'format']
    }
)
@validated("apply_conditional_formatting")
def apply_conditional_formatting_handler(event, context, current_user, name, data):
    return common_handler(apply_conditional_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'condition', 'format')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/sheets/execute-query",
    tags=["default"],
    name="executeQuery",
    description="Executes a SQL-like query on a Google Sheets spreadsheet.",
    parameters={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "query": {
                "type": "string",
                "description": "The SQL-like query to execute e.g., a == 1 and b < 34"
            },
            "sheetName": {
                "type": "string",
                "description": "The name of the sheet to query (omit for first sheet)",
            }
        },
        "required": ["spreadsheetId", "query"]
    }
)
@validated("execute_query")
def execute_query_handler(event, context, current_user, name, data):
    return common_handler(execute_query, 'spreadsheetId', 'query', sheetName='sheetName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/create-document",
    tags=["default"],
    name="createNewDocument",
    description="Creates a new Google Docs document.",
    params={
        "title": "The title of the new document"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title of the new document"
            }
        },
        "required": ["title"]
    }
)
@validated("create_new_document")
def create_new_document_handler(event, context, current_user, name, data):
    return common_handler(create_new_document, 'title')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/get-contents",
    tags=["default"],
    name="getDocumentContents",
    description="Retrieves the contents of a Google Docs document.",
    params={
        "documentId": "The ID of the document"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            }
        },
        "required": ["documentId"]
    }
)
@validated("get_document_contents")
def get_document_contents_handler(event, context, current_user, name, data):
    return common_handler(get_document_contents, 'documentId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/insert-text",
    tags=["default"],
    name="insertText",
    description="Inserts text at a specific location in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "text": "The text to insert",
        "index": "The index at which to insert the text"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "text": {
                "type": "string",
                "description": "The text to insert"
            },
            'index': {
                'type': 'integer',
                'description': 'The index at which to insert the text'
            }
        },
        'required': ['documentId', 'text', 'index']
    }
)
@validated("insert_text")
def insert_text_handler(event, context, current_user, name, data):
    return common_handler(insert_text, 'documentId', 'text', 'index')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/append-text",
    tags=["default"],
    name="appendText",
    description="Appends text to the end of a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "text": "The text to append"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "text": {
                "type": "string",
                "description": "The text to append"
            }
        },
        'required': ['documentId', 'text']
    }
)
@validated("append_text")
def append_text_handler(event, context, current_user, name, data):
    return common_handler(append_text, 'documentId', 'text')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/replace-text",
    tags=["default"],
    name="replaceText",
    description="Replaces all occurrences of text in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "oldText": "The text to be replaced",
        "newText": "The text to replace with"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "oldText": {
                "type": "string",
                "description": "The text to be replaced"
            },
            'newText': {
                'type': 'string',
                'description': 'The text to replace with'
            }
        },
        'required': ['documentId', 'oldText', 'newText']
    }
)
@validated("replace_text")
def replace_text_handler(event, context, current_user, name, data):
    return common_handler(replace_text, 'documentId', 'oldText', 'newText')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/create-outline",
    tags=["default"],
    name="createDocumentOutline",
    description="Creates an outline in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "outlineItems": "An array of objects with 'start' and 'end' indices for each outline item"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "outlineItems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "integer",
                            "description": "The start index of the outline item"
                        },
                        "end": {
                            "type": "integer",
                            "description": "The end index of the outline item"
                        }
                    },
                    "required": ["start", "end"]
                },
                "description": "An array of objects with 'start' and 'end' indices for each outline item"
            }
        },
        'required': ['documentId', 'outlineItems']
    }
)
@validated("create_document_outline")
def create_document_outline_handler(event, context, current_user, name, data):
    return common_handler(create_document_outline, 'documentId', 'outlineItems')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/export-document",
    tags=["default"],
    name="exportDocument",
    description="Exports a Google Docs document to a specified format.",
    params={
        "documentId": "The ID of the document",
        "mimeType": "The MIME type of the format to export to"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "mimeType": {
                "type": "string",
                "description": "The MIME type of the format to export to"
            }
        },
        'required': ['documentId', 'mimeType']
    }
)
@validated("export_document")
def export_document_handler(event, context, current_user, name, data):
    return common_handler(export_document, 'documentId', 'mimeType')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/share-document",
    tags=["default"],
    name="shareDocument",
    description="Shares a Google Docs document with another user.",
    params={
        "documentId": "The ID of the document",
        "email": "The email address of the user to share with",
        "role": "The role to grant to the user (e.g., 'writer', 'reader')"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "email": {
                "type": "string",
                "description": "The email address of the user to share with"
            },
            'role': {
                'type': 'string',
                'description': 'The role to grant to the user (e.g., \'writer\', \'reader\')'
            }
        },
        'required': ['documentId', 'email', 'role']
    }
)
@validated("share_document")
def share_document_handler(event, context, current_user, name, data):
    return common_handler(share_document, 'documentId', 'email', 'role')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/docs/find-text-indices",
    tags=["default"],
    name="findTextIndices",
    description="Finds the indices of a specific text in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "searchText": "The text to search for"
    },
    parameters={
        "type": "object",
        "properties": {
            "documentId": {
                "type": "string",
                "description": "The ID of the document"
            },
            "searchText": {
                "type": "string",
                "description": "The text to search for"
            }
        },
        'required': ['documentId', 'searchText']
    }
)
@validated("find_text_indices")
def find_text_indices_handler(event, context, current_user, name, data):
    return common_handler(find_text_indices, 'documentId', 'searchText')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-events-between-dates",
    tags=["default"],
    name="getEventsBetweenDates",
    description="Retrieves events from Google Calendar between two specified dates.",
    params={
        "startDate": "The start date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)",
        "endDate": "The end date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)",
        "includeDescription": "Optional. Include event description (default: false)",
        "includeAttendees": "Optional. Include event attendees (default: false)",
        "includeLocation": "Optional. Include event location (default: false)"
    },
    parameters={
        "type": "object",
        "properties": {
            "startDate": {
                "type": "string",
                "description": "The start date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)"
            },
            "endDate": {
                "type": "string",
                "description": "The end date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)"
            },
            "includeDescription": {
                "type": ["boolean"],
                "description": "Optional. Include event description (default: false)",
                "default": False
            },
            "includeAttendees": {
                "type": ["boolean"],
                "description": "Optional. Include event attendees (default: false)",
                "default": False
            },
            'includeLocation': {
                'type': ['boolean'],
                'description': 'Optional. Include event location (default: false)',
                "default": False
            }
        },
        'required': ['startDate', 'endDate']
    }
)
@validated("get_events_between_dates")
def get_events_between_dates_handler(event, context, current_user, name, data):
    return common_handler(get_events_between_dates, 'startDate', 'endDate', includeDescription=False, includeAttendees=False, includeLocation=False)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/create-event",
    tags=["default"],
    name="createEvent",
    description="Creates a new event in Google Calendar.",
    params={
        "title": "The title of the event",
        "startTime": "The start time of the event in ISO 8601 format",
        "endTime": "The end time of the event in ISO 8601 format",
        "description": "The description of the event"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title of the event"
            },
            "startTime": {
                "type": "string",
                "description": "The start time of the event in ISO 8601 format"
            },
            "endTime": {
                "type": "string",
                "description": "The end time of the event in ISO 8601 format"
            },
            'description': {
                'type': ['string'],
                'description': 'Optional: The description of the event'
            },
            'attendees': {
                'type': ['array'],
                'description': 'Optional: List of attendees\' email addresses'
            },
        },
        'required': ['title', 'startTime', 'endTime', 'description']
    }
)
@validated("create_event")
def create_event_handler(event, context, current_user, name, data):
    return common_handler(create_event, 'title', 'startTime', 'endTime', 'description', attendees=[])(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/update-event",
    tags=["default"],
    name="updateEvent",
    description="Updates an existing event in Google Calendar.",
    params={
        "eventId": "The ID of the event to update",
        "updatedFields": "A dictionary of fields to update and their new values"
    },
    parameters={
        "type": "object",
        "properties": {
            "eventId": {
                "type": "string",
                "description": "The ID of the event to update"
            },
            "updatedFields": {
                "type": "object",
                "description": "A dictionary of fields to update and their new values"
            }
        },
        'required': ['eventId', 'updatedFields']
    }
)
@validated("update_event")
def update_event_handler(event, context, current_user, name, data):
    return common_handler(update_event, 'eventId', 'updatedFields')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/delete-event",
    tags=["default"],
    name="deleteEvent",
    description="Deletes an event from Google Calendar.",
    params={
        "eventId": "The ID of the event to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "eventId": {
                "type": "string",
                "description": "The ID of the event to delete"
            }
        },
        'required': ['eventId']
    }
)
@validated("delete_event")
def delete_event_handler(event, context, current_user, name, data):
    return common_handler(delete_event, 'eventId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-event-details",
    tags=["default"],
    name="getEventDetails",
    description="Retrieves details of a specific event from Google Calendar.",
    params={
        "eventId": "The ID of the event to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "eventId": {
                "type": "string",
                "description": "The ID of the event to retrieve"
            }
        },
        'required': ['eventId']
    }
)
@validated("get_event_details")
def get_event_details_handler(event, context, current_user, name, data):
    return common_handler(get_event_details, 'eventId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-events-for-date",
    tags=["default"],
    name="getEventsForDate",
    description="Retrieves events from Google Calendar for a specific date.",
    params={
        "date": "The date in ISO 8601 format (YYYY-MM-DD)",
        "includeDescription": "Optional. Include event description (default: false)",
        "includeAttendees": "Optional. Include event attendees (default: false)",
        "includeLocation": "Optional. Include event location (default: false)"
    },
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "The date in ISO 8601 format (YYYY-MM-DD)"
            },
            "includeDescription": {
                "type": ["boolean"],
                "description": "Optional. Include event description (default: false)",
                "default": False
            },
            "includeAttendees": {
                "type": ["boolean"],
                "description": "Optional. Include event attendees (default: false)",
                "default": False
            },
            'includeLocation': {
                'type': ['boolean'],
                'description': 'Optional. Include event location (default: false)',
                "default": False
            }
        },
        'required': ['date']
    }
)
@validated("get_events_for_date")
def get_events_for_date_handler(event, context, current_user, name, data):
    return common_handler(get_events_for_date, 'date', includeDescription=False, includeAttendees=False, includeLocation=False)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-free-time-slots",
    tags=["default"],
    name="getFreeTimeSlots",
    description="Finds free time slots in Google Calendar between two dates.",
    params={
        "startDate": "The start date in ISO 8601 format",
        "endDate": "The end date in ISO 8601 format",
        "duration": "The minimum duration of free time slots in minutes",
        'userTimeZone': "Optional. The time zone of the user (default: 'America/Chicago')"
    },
    parameters={
        "type": "object",
        "properties": {
            "startDate": {
                "type": "string",
                "description": "The start date in ISO 8601 format"
            },
            "endDate": {
                "type": "string",
                "description": "The end date in ISO 8601 format"
            },
            "duration": {
                "type": "integer",
                "description": "The minimum duration of free time slots in minutes"
            },
            'userTimeZone': {
                'type': ['string'],
                'description': 'Optional. The time zone of the user (default: \'America/Chicago\')',
                "default": "America/Chicago"
            }
        },
        'required': ['startDate', 'endDate', 'duration']
    }
)
@validated("get_free_time_slots")
def get_free_time_slots_handler(event, context, current_user, name, data):
    return common_handler(get_free_time_slots, 'startDate', 'endDate', 'duration', userTimeZone="America/Chicago")(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/check-event-conflicts",
    tags=["default"],
    name="checkEventConflicts",
    description="Checks for conflicts with existing events in Google Calendar.",
    params={
        "proposedStartTime": "The proposed start time in ISO 8601 format",
        "proposedEndTime": "The proposed end time in ISO 8601 format",
        "returnConflictingEvents": "Optional. Return details of conflicting events (default: false)"
    },
    parameters={
        "type": "object",
        "properties": {
            "proposedStartTime": {
                "type": "string",
                "description": "The proposed start time in ISO 8601 format"
            },
            "proposedEndTime": {
                "type": "string",
                "description": "The proposed end time in ISO 8601 format"
            },
            'returnConflictingEvents': {
                'type': ['boolean'],
                'description': 'Optional. Return details of conflicting events (default: false)',
                "default": False
            }
        },
        'required': ['proposedStartTime', 'proposedEndTime']
    }
)
@validated("check_event_conflicts")
def check_event_conflicts_handler(event, context, current_user, name, data):
    return common_handler(check_event_conflicts, 'proposedStartTime', 'proposedEndTime', returnConflictingEvents=False)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/list-files",
    tags=["default"],
    name="listFiles",
    description="Lists files in a specific folder or root directory of Google Drive.",
    params={
        "folderId": "The ID of the folder to list files from (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "folderId": {
                "type": ["string"],
                "description": "The ID of the folder to list files from (optional)"
            }
        },
        'required': []
    }
)
@validated("list_files")
def list_files_handler(event, context, current_user, name, data):
    return common_handler(list_files, 'folderId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/search-files",
    tags=["default"],
    name="searchFiles",
    description="Searches for files in Google Drive based on a query. You should know that \"name contains '<query>'\" is added automatically to the query.",
    params={
        "query": "The search query string"
    },
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string"
            }
        },
        'required': ['query']
    }
)
@validated("search_files")
def search_files_handler(event, context, current_user, name, data):
    return common_handler(search_files, 'query')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/get-file-metadata",
    tags=["default"],
    name="getFileMetadata",
    description="Retrieves metadata for a specific file in Google Drive.",
    params={
        "fileId": "The ID of the file"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_file_metadata")
def get_file_metadata_handler(event, context, current_user, name, data):
    return common_handler(get_file_metadata, 'fileId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/get-file-content",
    tags=["default"],
    name="getFileContent",
    description="Gets the content of a file in Google Drive as text.",
    params={
        "fileId": "The ID of the file"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_file_content")
def get_file_content_handler(event, context, current_user, name, data):
    return common_handler(get_file_content, 'fileId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/create-file",
    tags=["default"],
    name="createFile",
    description="Creates a new file in Google Drive with the given content.",
    params={
        "fileName": "The name of the file to create",
        "content": "The content of the file",
        "mimeType": "The MIME type of the file (optional, defaults to 'text/plain')"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileName": {
                "type": "string",
                "description": "The name of the file to create"
            },
            "content": {
                "type": "string",
                "description": "The content of the file"
            },
            'mimeType': {
                'type': ['string'],
                'description': 'Optional: The MIME type of the file (default: \'text/plain\')',
                "default": "text/plain"
            }
        },
        'required': ['fileName', 'content']
    }
)
@validated("create_file")
def create_file_handler(event, context, current_user, name, data):
    return common_handler(create_file, 'fileName', 'content', 'mimeType')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/get-download-link",
    tags=["default"],
    name="getDownloadLink",
    description="Gets the download link for a file in Google Drive.",
    params={
        "fileId": "The ID of the file"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_download_link")
def get_download_link_handler(event, context, current_user, name, data):
    return common_handler(get_download_link, 'fileId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/create-shared-link",
    tags=["default"],
    name="createSharedLink",
    description="Creates a shared link for a file in Google Drive with view or edit permissions.",
    params={
        "fileId": "The ID of the file",
        "permission": "The permission level ('view' or 'edit')"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file"
            },
            "permission": {
                "type": "string",
                "description": "The permission level ('view' or 'edit')"
            }
        },
        'required': ['fileId', 'permission']
    }
)
@validated("create_shared_link")
def create_shared_link_handler(event, context, current_user, name, data):
    return common_handler(create_shared_link, 'fileId', 'permission')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/share-file",
    tags=["default"],
    name="shareFile",
    description="Shares a file in Google Drive with multiple email addresses.",
    params={
        "fileId": "The ID of the file",
        "emails": "List of email addresses to share the file with",
        "role": "The role to assign ('reader', 'commenter', or 'writer')"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file"
            },
            "emails": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "List of email addresses to share the file with"
            },
            'role': {
                'type': 'string',
                'description': 'The role to assign (\'reader\', \'commenter\', or \'writer\')'
            }
        },
        'required': ['fileId', 'emails', 'role']
    }
)
@validated("share_file")
def share_file_handler(event, context, current_user, name, data):
    return common_handler(share_file, 'fileId', 'emails', 'role')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/convert-file",
    tags=["default"],
    name="convertFile",
    description="Converts a file in Google Drive to a specified format and returns its download link.",
    params={
        "fileId": "The ID of the file to convert",
        "targetMimeType": "The target MIME type for conversion"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file to convert"
            },
            "targetMimeType": {
                "type": "string",
                "description": "The target MIME type for conversion"
            }
        },
        'required': ['fileId', 'targetMimeType']
    }
)
@validated("convert_file")
def convert_file_handler(event, context, current_user, name, data):
    return common_handler(convert_file, 'fileId', 'targetMimeType')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/list-folders",
    tags=["default"],
    name="listFolders",
    description="Lists folders in Google Drive, optionally within a specific parent folder.",
    params={
        "parentFolderId": "The ID of the parent folder (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "parentFolderId": {
                "type": ["string"],
                "description": "The ID of the parent folder (optional)"
            }
        },
        'required': []
    }
)
@validated("list_folders")
def list_folders_handler(event, context, current_user, name, data):
    return common_handler(list_folders, 'parentFolderId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/move-item",
    tags=["default"],
    name="moveItem",
    description="Moves a file or folder to a specified destination folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to move",
        "destinationFolderId": "The ID of the destination folder"
    },
    parameters={
        "type": "object",
        "properties": {
            "itemId": {
                "type": "string",
                "description": "The ID of the file or folder to move"
            },
            "destinationFolderId": {
                "type": "string",
                "description": "The ID of the destination folder"
            }
        },
        'required': ['itemId', 'destinationFolderId']
    }
)
@validated("move_item")
def move_item_handler(event, context, current_user, name, data):
    return common_handler(move_item, 'itemId', 'destinationFolderId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/copy-item",
    tags=["default"],
    name="copyItem",
    description="Copies a file or folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to copy",
        "newName": "The name for the copied item (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "itemId": {
                "type": "string",
                "description": "The ID of the file or folder to copy"
            },
            "newName": {
                "type": ["string"],
                "description": "The name for the copied item (optional)"
            }
        },
        'required': ['itemId']
    }
)
@validated("copy_item")
def copy_item_handler(event, context, current_user, name, data):
    return common_handler(copy_item, 'itemId', 'newName')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/rename-item",
    tags=["default"],
    name="renameItem",
    description="Renames a file or folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to rename",
        "newName": "The new name for the item"
    },
    parameters={
        "type": "object",
        "properties": {
            "itemId": {
                "type": "string",
                "description": "The ID of the file or folder to rename"
            },
            "newName": {
                "type": "string",
                "description": "The new name for the item"
            }
        },
        'required': ['itemId', 'newName']
    }
)
@validated("rename_item")
def rename_item_handler(event, context, current_user, name, data):
    return common_handler(rename_item, 'itemId', 'newName')(event, context, current_user, name, data)



@vop(
    path="/google/integrations/drive/get-file-revisions",
    tags=["default"],
    name="getFileRevisions",
    description="Gets the revision history of a file in Google Drive.",
    params={
        "fileId": "The ID of the file to get revisions for"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file to get revisions for"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_file_revisions")
def get_file_revisions_handler(event, context, current_user, name, data):
    return common_handler(get_file_revisions, 'fileId')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/drive/create-folder",
    tags=["default"],
    name="createFolder",
    description="Creates a new folder in Google Drive.",
    params={
        "folderName": "The name of the new folder",
        "parentId": "The ID of the parent folder (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "folderName": {
                "type": "string",
                "description": "The name of the new folder"
            },
            "parentId": {
                "type": ["string"],
                "description": "The ID of the parent folder (optional)"
            }
        },
        'required': ['folderName']
    }
)
@validated("create_folder")
def create_folder_handler(event, context, current_user, name, data):
    return common_handler(create_folder, 'folderName', 'parentId')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/drive/get-file-revisions",
    tags=["default"],
    name="getFileRevisions",
    description="Gets the revision history of a file in Google Drive.",
    params={
        "fileId": "The ID of the file to get revisions for"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file to get revisions for"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_file_revisions")
def get_file_revisions_handler(event, context, current_user, name, data):
    return common_handler(get_file_revisions, 'fileId')(event, context, current_user, name, data)



@vop(
    path="/google/integrations/drive/create-folder",
    tags=["default"],
    name="createFolder",
    description="Creates a new folder in Google Drive.",
    params={
        "folderName": "The name of the new folder",
        "parentId": "The ID of the parent folder (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "folderName": {
                "type": "string",
                "description": "The name of the new folder"
            },
            "parentId": {
                "type": ["string"],
                "description": "The ID of the parent folder (optional)"
            }
        },
        'required': ['folderName']
    }
)
@validated("create_folder")
def create_folder_handler(event, context, current_user, name, data):
    return common_handler(create_folder, 'folderName', 'parentId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/drive/get-file-revisions",
    tags=["default"],
    name="getFileRevisions",
    description="Gets the revision history of a file in Google Drive.",
    params={
        "fileId": "The ID of the file to get revisions for"
    },
    parameters={
        "type": "object",
        "properties": {
            "fileId": {
                "type": "string",
                "description": "The ID of the file to get revisions for"
            }
        },
        'required': ['fileId']
    }
)
@validated("get_file_revisions")
def get_file_revisions_handler(event, context, current_user, name, data):
    return common_handler(get_file_revisions, 'fileId')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/drive/delete-item-permanently",
    tags=["default"],
    name="deleteItemPermanently",
    description="Permanently deletes a file or folder from Google Drive.",
    params={
        "itemId": "The ID of the file or folder to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "itemId": {
                "type": "string",
                "description": "The ID of the file or folder to delete"
            }
        },
        'required': ['itemId']
    }
)
@validated("delete_item_permanently")
def delete_item_permanently_handler(event, context, current_user, name, data):
    return common_handler(delete_item_permanently, 'itemId')(event, context, current_user, name, data)


@vop(
    path="/google/integrations/drive/get-root-folder-ids",
    tags=["default"],
    name="getRootFolderIds",
    description="Retrieves the IDs of root-level folders in Google Drive.",
    params={
    },
    parameters={
        "type": "object",
        "properties": {}
    }
)
@validated("get_root_folder_ids")
def get_root_folder_ids_handler(event, context, current_user, name, data):
    return common_handler(get_root_folder_ids)(event, context, current_user, name, data)


@vop(
    path="/google/integrations/forms/create-form",
    tags=["default"],
    name="createForm",
    description="Creates a new Google Form.",
    params={
        "title": "The title of the new form",
        "description": "Optional description for the form"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title of the new form"
            },
            "description": {
                "type": ["string"],
                "description": "Optional description for the form"
            }
        },
        'required': ['title']
    }
)
@validated("create_form")
def create_form_handler(event, context, current_user, name, data):
    return common_handler(create_form, 'title', description="")(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/get-form-details",
    tags=["default"],
    name="getFormDetails",
    description="Retrieves details of a specific Google Form.",
    params={
        "formId": "The ID of the form to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form to retrieve"
            }
        },
        'required': ['formId']
    }
)
@validated("get_form_details")
def get_form_details_handler(event, context, current_user, name, data):
    return common_handler(get_form_details, 'formId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/add-question",
    tags=["default"],
    name="addQuestion",
    description="Adds a new question to a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionType": "The type of question (e.g., 'TEXT', 'MULTIPLE_CHOICE', 'CHECKBOX')",
        "title": "The title of the question",
        "required": "Whether the question is required (default: false)",
        "options": "List of options for multiple choice or checkbox questions (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            "questionType": {
                "type": "string",
                "description": "The type of question (e.g., 'TEXT', 'MULTIPLE_CHOICE', 'CHECKBOX')"
            },
            "title": {
                "type": "string",
                "description": "The title of the question"
            },
            'required': {
                'type': ['boolean'],
                'description': 'Optional: Whether the question is required (default: false)',
                "default": False
            },
            'options': {
                'type': ['array'],
                'items': {'type': 'string'},
                'description': 'Optional: List of options for multiple choice or checkbox questions'
            }
        },
        'required': ['formId', 'questionType', 'title']
    }
)
@validated("add_question")
def add_question_handler(event, context, current_user, name, data):
    return common_handler(add_question, 'formId', 'questionType', 'title', required=False, options=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/update-question",
    tags=["default"],
    name="updateQuestion",
    description="Updates an existing question in a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionId": "The ID of the question to update",
        "title": "The new title of the question (optional)",
        "required": "Whether the question is required (optional)",
        "options": "New list of options for multiple choice or checkbox questions (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            "questionId": {
                "type": "string",
                "description": "The ID of the question to update"
            },
            'title': {
                'type': ['string'],
                'description': 'Optional: The new title of the question'
            },
            'required': {
                'type': ['boolean'],
                'description': 'Optional: Whether the question is required',
                "default": False
            },
            'options': {
                'type': ['array'],
                'items': {'type': 'string'},
                'description': 'Optional: New list of options for multiple choice or checkbox questions'
            }
        },
        'required': ['formId', 'questionId']
    }
)
@validated("update_question")
def update_question_handler(event, context, current_user, name, data):
    return common_handler(update_question, 'formId', 'questionId', title=None, required=None, options=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/delete-question",
    tags=["default"],
    name="deleteQuestion",
    description="Deletes a question from a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionId": "The ID of the question to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            "questionId": {
                "type": "string",
                "description": "The ID of the question to delete"
            }
        },
        'required': ['formId', 'questionId']
    }
)
@validated("delete_question")
def delete_question_handler(event, context, current_user, name, data):
    return common_handler(delete_question, 'formId', 'questionId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/get-responses",
    tags=["default"],
    name="getResponses",
    description="Retrieves all responses for a Google Form.",
    params={
        "formId": "The ID of the form"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            }
        },
        'required': ['formId']
    }
)
@validated("get_responses")
def get_responses_handler(event, context, current_user, name, data):
    return common_handler(get_responses, 'formId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/get-response",
    tags=["default"],
    name="getResponse",
    description="Retrieves a specific response from a Google Form.",
    params={
        "formId": "The ID of the form",
        "responseId": "The ID of the response to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            "responseId": {
                "type": "string",
                "description": "The ID of the response to retrieve"
            }
        },
        'required': ['formId', 'responseId']
    }
)
@validated("get_response")
def get_response_handler(event, context, current_user, name, data):
    return common_handler(get_response, 'formId', 'responseId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/set-form-settings",
    tags=["default"],
    name="setFormSettings",
    description="Updates the settings of a Google Form.",
    params={
        "formId": "The ID of the form",
        "settings": "A dictionary of settings to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            "settings": {
                "type": "object",
                "description": "A dictionary of settings to update"
            }
        },
        'required': ['formId', 'settings']
    }
)
@validated("set_form_settings")
def set_form_settings_handler(event, context, current_user, name, data):
    return common_handler(set_form_settings, 'formId', 'settings')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/get-form-link",
    tags=["default"],
    name="getFormLink",
    description="Retrieves the public link for a Google Form.",
    params={
        "formId": "The ID of the form"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            }
        },
        'required': ['formId']
    }
)
@validated("get_form_link")
def get_form_link_handler(event, context, current_user, name, data):
    return common_handler(get_form_link, 'formId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/update-form-info",
    tags=["default"],
    name="updateFormInfo",
    description="Updates the title and/or description of a Google Form.",
    params={
        "formId": "The ID of the form",
        "title": "The new title for the form (optional)",
        "description": "The new description for the form (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "formId": {
                "type": "string",
                "description": "The ID of the form"
            },
            'title': {
                'type': ['string'],
                'description': 'Optional: The new title for the form'
            },
            'description': {
                'type': ['string'],
                'description': 'Optional: The new description for the form'
            }
        },
        'required': ['formId']
    }
)
@validated("update_form_info")
def update_form_info_handler(event, context, current_user, name, data):
    return common_handler(update_form_info, 'formId', title=None, description=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/forms/list-user-forms",
    tags=["default"],
    name="listUserForms",
    description="Lists all forms owned by the current user.",
    params={},
    parameters={
        "type": "object",
        "properties": {}
    }
)
@validated("list_user_forms")
def list_user_forms_handler(event, context, current_user, name, data):
    return common_handler(list_user_forms)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/compose-and-send",
    tags=["default"],
    name="composeAndSendEmail",
    description="Composes and sends an email, with an option to schedule for future.",
    params={
        "to": "Recipient email address(es) as a string, comma-separated for multiple recipients",
        "subject": "Email subject",
        "body": "Email body content",
        "cc": "Optional: CC recipient(s) email address(es)",
        "bcc": "Optional: BCC recipient(s) email address(es)",
        "scheduleTime": "Optional: ISO format datetime string for scheduled sending"
    },
    parameters={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address(es) as a string, comma-separated for multiple recipients"
            },
            "subject": {
                "type": "string",
                "description": "Email subject"
            },
            "body": {
                "type": "string",
                "description": "Email body content"
            },
            'cc': {
                'type': ['string'],
                'description': 'Optional: CC recipient(s) email address(es)'
            },
            'bcc': {
                'type': ['string'],
                'description': 'Optional: BCC recipient(s) email address(es)'
            },
            'scheduleTime': {
                'type': ['string'],
                'description': 'Optional: ISO format datetime string for scheduled sending'
            }
        },
        'required': ['to', 'subject', 'body']
    }
)
@validated("compose_and_send_email")
def compose_and_send_email_handler(event, context, current_user, name, data):
    return common_handler(compose_and_send_email, 'to', 'subject', 'body', cc=None, bcc=None, schedule_time=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/compose-draft",
    tags=["default"],
    name="composeEmailDraft",
    description="Composes an email draft.",
    parameters={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address(es), comma-separated for multiple. Must be valid email addresses."
            },
            "subject": {
                "type": "string",
                "description": "Email subject"
            },
            "body": {
                "type": "string",
                "description": "Email body content"
            },
            "cc": {
                "type": "string",
                "description": "CC recipient(s) email address(es)"
            },
            "bcc": {
                "type": "string",
                "description": "BCC recipient(s) email address(es)",
            }
        },
        "required": ["to", "subject", "body"]
    }
)
@validated("compose_email_draft")
def compose_email_draft_handler(event, context, current_user, name, data):
    return common_handler(compose_email_draft, 'to', 'subject', 'body', cc=None, bcc=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/get-messages-from-date",
    tags=["default"],
    name="getMessagesFromDate",
    description="Gets messages from a specific start date (optional label).",
    params={
        "n": "Number of messages to retrieve",
        "startDate": "Start date in YYYY-MM-DD format",
        "label": "Optional: Label to filter messages"
    },
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of messages to retrieve"
            },
            "startDate": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format"
            },
            'label': {
                'type': ['string'],
                'description': 'Optional: Label to filter messages'
            }
        },
        'required': ['n', 'startDate']
    }
)
@validated("get_messages_from_date")
def get_messages_from_date_handler(event, context, current_user, name, data):
    return common_handler(get_messages_from_date, 'n', 'start_date', label=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/get-recent-messages",
    tags=["default"],
    name="getRecentMessages",
    description="Gets the N most recent messages (optional label).",
    params={
        "n": "Number of messages to retrieve (default 25)",
        "label": "Optional: Label to filter messages"
    },
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of messages to retrieve",
                "default": 25
            },
            "label": {
                "type": "string",
                "description": "Optional: Label to filter messages"
            }
        }
    }
)
@validated("get_recent_messages")
def get_recent_messages_handler(event, context, current_user, name, data):
    return common_handler(get_recent_messages, n=25, label=None)(event, context, current_user, name, data)


@vop(
    path="/google/integrations/gmail/search-messages",
    tags=["default"],
    name="searchMessages",
    description="Searches for messages using the Gmail search language.",
    params={
        "query": "Search query string using Gmail search language"
    },
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string using Gmail search language"
            }
        },
        'required': ['query']
    }
)
@validated("search_messages")
def search_messages_handler(event, context, current_user, name, data):
    return common_handler(search_messages, 'query')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/get-attachment-links",
    tags=["default"],
    name="getAttachmentLinks",
    description="Gets links to download attachments for a specific email.",
    params={
        "messageId": "ID of the email message"
    },
    parameters={
        "type": "object",
        "properties": {
            "messageId": {
                "type": "string",
                "description": "ID of the email message"
            }
        },
        'required': ['messageId']
    }
)
@validated("get_attachment_links")
def get_attachment_links_handler(event, context, current_user, name, data):
    return common_handler(get_attachment_links, 'message_id')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/get-attachment-content",
    tags=["default"],
    name="getAttachmentContent",
    description="Gets the content of a specific attachment.",
    params={
        "messageId": "ID of the email message",
        "attachmentId": "ID of the attachment"
    },
    parameters={
        "type": "object",
        "properties": {
            "messageId": {
                "type": "string",
                "description": "ID of the email message"
            },
            "attachmentId": {
                "type": "string",
                "description": "ID of the attachment"
            }
        },
        'required': ['messageId', 'attachmentId']
    }
)
@validated("get_attachment_content")
def get_attachment_content_handler(event, context, current_user, name, data):
    return common_handler(get_attachment_content, 'message_id', 'attachment_id')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/create-filter",
    tags=["default"],
    name="createFilter",
    description="Creates a new email filter.",
    params={
        "criteria": "Filter criteria as a dictionary",
        "action": "Action to take when filter criteria are met, as a dictionary"
    },
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": "Filter criteria as a dictionary"
            },
            "action": {
                "type": "object",
                "description": "Action to take when filter criteria are met, as a dictionary"
            }
        },
        'required': ['criteria', 'action']
    }
)
@validated("create_filter")
def create_filter_handler(event, context, current_user, name, data):
    return common_handler(create_filter, 'criteria', 'action')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/create-label",
    tags=["default"],
    name="createLabel",
    description="Creates a new label.",
    params={
        "name": "Name of the new label"
    },
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the new label"
            }
        },
        'required': ['name']
    }
)
@validated("create_label")
def create_label_handler(event, context, current_user, name, data):
    return common_handler(create_label, 'name')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/create-auto-filter-label-rule",
    tags=["default"],
    name="createAutoFilterLabelRule",
    description="Creates an auto-filter and label rule.",
    params={
        "criteria": "Filter criteria as a dictionary",
        "labelName": "Name of the label to apply"
    },
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": "Filter criteria as a dictionary"
            },
            "labelName": {
                "type": "string",
                "description": "Name of the label to apply"
            }
        },
        'required': ['criteria', 'labelName']
    }
)
@validated("create_auto_filter_label_rule")
def create_auto_filter_label_rule_handler(event, context, current_user, name, data):
    return common_handler(create_auto_filter_label_rule, 'criteria', 'label_name')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/gmail/get-message-details",
    tags=["default"],
    name="getMessageDetails",
    description="Gets detailed information, such as body, bcc, sent date, etc. for one or more Gmail messages.",
    params={
        "message_id": "ID of the message to retrieve details for",
        "fields": "Optional: List of fields to include in the response. Default is (id, sender, subject, labels, date). Full list is (id, threadId, historyId, sizeEstimate, raw, payload, mimeType, attachments, sender, subject, labels, date, snippet, body, cc, bcc, deliveredTo, receivedTime, sentTime)"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "ID of the message to retrieve details for"
            },
            "fields": {
                "type": ["array"],
                "items": {"type": "string"},
                "description": "Optional: List of fields to include in the response. Default is (id, sender, subject, labels, date). Full list is (id, threadId, historyId, sizeEstimate, raw, payload, mimeType, attachments, sender, subject, labels, date, snippet, body, cc, bcc, deliveredTo, receivedTime, sentTime)",
                "default": ["id", "sender", "subject", "labels", "date"]
            }
        },
        'required': ['message_id']
    }
)
@validated("get_message_details")
def get_message_details_handler(event, context, current_user, name, data):
    return common_handler(get_messages_details, message_id=None, fields=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/search-contacts",
    tags=["default"],
    name="searchContacts",
    description="Searches the user's Google Contacts.",
    params={
        "query": "Search query string",
        "page_size": "Optional: Number of results to return (default 10)"
    },
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string"
            },
            'page_size': {
                'type': ['integer'],
                'description': 'Optional: Number of results to return (default 10)',
                'default': 10
            }
        },
        'required': ['query']
    }
)
@validated("search_contacts")
def search_contacts_handler(event, context, current_user, name, data):
    return common_handler(search_contacts, query=None, page_size=10)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/get-contact-details",
    tags=["default"],
    name="getContactDetails",
    description="Gets details for a specific contact.",
    params={
        "resource_name": "Resource name of the contact"
    },
    parameters={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Resource name of the contact"
            }
        },
        'required': ['resource_name']
    }
)
@validated("get_contact_details")
def get_contact_details_handler(event, context, current_user, name, data):
    return common_handler(get_contact_details, resource_name=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/create-contact",
    tags=["default"],
    name="createContact",
    description="Creates a new contact.",
    params={
        "contact_info": "Contact information"
    },
    parameters={
        "type": "object",
        "properties": {
            "contact_info": {
                "type": "object",
                "description": "Contact information"
            }
        },
        'required': ['contact_info']
    }
)
@validated("create_contact")
def create_contact_handler(event, context, current_user, name, data):
    return common_handler(create_contact, contact_info=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/update-contact",
    tags=["default"],
    name="updateContact",
    description="Updates an existing contact.",
    params={
        "resource_name": "Resource name of the contact",
        "contact_info": "Updated contact information"
    },
    parameters={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Resource name of the contact"
            },
            "contact_info": {
                "type": "object",
                "description": "Updated contact information"
            }
        },
        'required': ['resource_name', 'contact_info']
    }
)
@validated("update_contact")
def update_contact_handler(event, context, current_user, name, data):
    return common_handler(update_contact, resource_name=None, contact_info=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/delete-contact",
    tags=["default"],
    name="deleteContact",
    description="Deletes a contact.",
    params={
        "resource_name": "Resource name of the contact to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Resource name of the contact to delete"
            }
        },
        'required': ['resource_name']
    }
)
@validated("delete_contact")
def delete_contact_handler(event, context, current_user, name, data):
    return common_handler(delete_contact, resource_name=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/list-contact-groups",
    tags=["default"],
    name="listContactGroups",
    description="Lists all contact groups.",
    params={},
    parameters={
        "type": "object",
        "properties": {}
    }
)
@validated("list_contact_groups")
def list_contact_groups_handler(event, context, current_user, name, data):
    return common_handler(list_contact_groups)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/create-contact-group",
    tags=["default"],
    name="createContactGroup",
    description="Creates a new contact group.",
    params={
        "group_name": "Name of the new contact group"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_name": {
                "type": "string",
                "description": "Name of the new contact group"
            }
        },
        'required': ['group_name']
    }
)
@validated("create_contact_group")
def create_contact_group_handler(event, context, current_user, name, data):
    return common_handler(create_contact_group, group_name=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/update-contact-group",
    tags=["default"],
    name="updateContactGroup",
    description="Updates an existing contact group.",
    params={
        "resource_name": "Resource name of the contact group",
        "new_name": "New name for the contact group"
    },
    parameters={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Resource name of the contact group"
            },
            "new_name": {
                "type": "string",
                "description": "New name for the contact group"
            }
        },
        'required': ['resource_name', 'new_name']
    }
)
@validated("update_contact_group")
def update_contact_group_handler(event, context, current_user, name, data):
    return common_handler(update_contact_group, resource_name=None, new_name=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/delete-contact-group",
    tags=["default"],
    name="deleteContactGroup",
    description="Deletes a contact group.",
    params={
        "resource_name": "Resource name of the contact group to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Resource name of the contact group to delete"
            }
        },
        'required': ['resource_name']
    }
)
@validated("delete_contact_group")
def delete_contact_group_handler(event, context, current_user, name, data):
    return common_handler(delete_contact_group, resource_name=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/add-contacts-to-group",
    tags=["default"],
    name="addContactsToGroup",
    description="Adds contacts to a group.",
    params={
        "group_resource_name": "Resource name of the contact group",
        "contact_resource_names": "List of resource names of contacts to add"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_resource_name": {
                "type": "string",
                "description": "Resource name of the contact group"
            },
            "contact_resource_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of resource names of contacts to add"
            }
        },
        'required': ['group_resource_name', 'contact_resource_names']
    }
)
@validated("add_contacts_to_group")
def add_contacts_to_group_handler(event, context, current_user, name, data):
    return common_handler(add_contacts_to_group, group_resource_name=None, contact_resource_names=None)(event, context, current_user, name, data)

@vop(
    path="/google/integrations/people/remove-contacts-from-group",
    tags=["default"],
    name="removeContactsFromGroup",
    description="Removes contacts from a group.",
    params={
        "group_resource_name": "Resource name of the contact group",
        "contact_resource_names": "List of resource names of contacts to remove"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_resource_name": {
                "type": "string",
                "description": "Resource name of the contact group"
            },
            "contact_resource_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of resource names of contacts to remove"
            }
        },
        'required': ['group_resource_name', 'contact_resource_names']
    }
)
@validated("remove_contacts_from_group")
def remove_contacts_from_group_handler(event, context, current_user, name, data):
    return common_handler(remove_contacts_from_group, group_resource_name=None, contact_resource_names=None)(event, context, current_user, name, data)




