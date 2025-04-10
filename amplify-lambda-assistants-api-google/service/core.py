from common.ops import vop
from common.validate import validated
from integrations.google.forms import create_form, get_form_details, add_question, update_question, delete_question, \
    get_responses, get_response, set_form_settings, get_form_link, update_form_info, list_user_forms
from integrations.google.calendar import get_events_between_dates, create_event, check_event_conflicts, \
    get_free_time_slots, get_events_for_date, get_event_details, delete_event, update_event, list_calendars, create_calendar, delete_calendar, update_calendar_permissions, create_recurring_event, add_event_reminders, get_calendar_details, update_calendar
from integrations.google.docs import find_text_indices, append_text
from integrations.google.drive import convert_file, share_file, create_shared_link, get_download_link, create_file, \
    get_file_content, get_file_metadata, search_files, list_files, list_folders, move_item, copy_item, rename_item, \
    delete_item_permanently, get_file_revisions, create_folder, get_root_folder_ids
from integrations.google.gmail import get_message_details, compose_and_send_email, compose_email_draft, get_recent_messages, search_messages, \
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
from service.routes import route_data
from jsonschema import validate
from jsonschema.exceptions import ValidationError


import re


def camel_to_snake(name):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
    return snake

def common_handler(operation, *required_params, **optional_params):
    def handler(current_user, data):
        try:
            params = {camel_to_snake(param): data['data'][param] for param in required_params}
            params.update({camel_to_snake(param): data['data'].get(param) for param in optional_params})
            params['access_token'] = data['access_token']
            response = operation(current_user, **params)
            return {"success": True, "data": response}
        except MissingCredentialsError as me:
            return {"success": False, "error": str(me)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return handler


@validated("route")
def route_request(event, context, current_user, name, data):
    try:
        # First try to use path-based routing if available
        target_path_string = event.get('path', event.get('rawPath', ''))
        print(f"Route path: {target_path_string}")
        
        # Check if we have a direct path match in our route_data
        route_info = route_data.get(target_path_string, None)
        
        if not route_info:
            return {"success": False, "error": "Invalid path"}

        func_schema = route_info['schema'] or {}

        wrapper_schema = {
                "type": "object",
                "properties": {
                    "data": func_schema
                },
                "required": ["data"]
            }

        print("Validating request")
        try:
            validate(data, wrapper_schema)
            print("Request data validated")
        except ValidationError as e:
            raise ValueError(f"Invalid request: {str(e)}")
    
        service = "/google/integrations/"
        # If no op parameter, try to extract from the path
        op = None
        if target_path_string.startswith(service):
            op = target_path_string.split(service)[1]
        else:
            return {
                'success': False,
                'message': 'Invalid path'
            }
            
        print("Operation to execute: ", op)

        # Dynamically look up the handler function based on the operation name
        handler_name = f"{op}_handler"
        handler_func = globals().get(handler_name)
        
        if not handler_func:
            return {
                'success': False,
                'message': f'Invalid operation: {op}. No handler function found for {handler_name}'
            }

        print("Executing handler function...")
        return handler_func(current_user, data)
            
    except Exception as e:
        import traceback
        return {
            'success': False,
            'message': f'Error processing request: {str(e)}',
            'traceback': traceback.format_exc()
        }




@vop(
    path="/google/integrations/get_rows",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_read"],
    name="googleGetSpreadsheetRows",
    description="Returns the rows from a Google Sheet as JSON.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "cellRange": "The range of cells to read as a string, such as A1:A"
    },
    schema={
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
# @validated("get_rows")
def get_rows_handler(current_user, data):
    return common_handler(get_spreadsheet_rows, 'spreadsheetId', 'cellRange')(current_user, data)

@vop(
    path="/google/integrations/get_google_sheets_info",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_read"],
    name="googleGetGoogleSheetsInfo",
    description="Returns information about Google Sheets, including sheet names and sample data.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    },
    schema={
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
# @validated("get_google_sheets_info")
def get_google_sheets_info_handler(current_user, data):
    return common_handler(get_sheets_info, 'spreadsheetId')(current_user, data)

@vop(
    path="/google/integrations/get_sheet_names",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_read"],
    name="googleGetSheetNames",
    description="Returns the list of sheet names in a Google Sheets document.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string"
    },
    schema={
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
# @validated("get_sheet_names")
def get_sheet_names_handler(current_user, data):
    return common_handler(get_sheet_names, 'spreadsheetId')(current_user, data)

@vop(
    path="/google/integrations/insert_rows",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleInsertRows",
    description="Inserts multiple new rows into a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to insert",
        "sheetName": "Optional: The name of the sheet to insert into",
        "insertionPoint": "Optional: The row number to start insertion at"
    },
    schema={
        "type": "object",
        "properties": {
            "spreadsheetId": {
                "type": "string",
                "description": "The ID of the spreadsheet"
            },
            "rowsData": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {
                        "type": ["string", "number", "boolean"]
                    }
                },
                "description": "An array of arrays, each representing a row to insert, where each cell contains a string, number, or boolean. Make sure and create black entries for empty cells."
            },
            "sheetName": {
                "type": "string",
                "description": "The name of the sheet to insert into (leave blank for first sheet)"
            },
            "insertionPoint": {
                "type": "integer",
                "description": "The row number to start insertion at, default is 1"
            }
        },
        "required": ["spreadsheetId", "rowsData"]
    }
)
# @validated("insert_rows")
def insert_rows_handler(current_user, data):
    return common_handler(insert_rows, 'spreadsheetId', 'rowsData', sheetName=None, insertionPoint=None)(current_user, data)

@vop(
    path="/google/integrations/delete_rows",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleDeleteRows",
    description="Deletes a range of rows from a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "startRow": "The first row to delete",
        "endRow": "The last row to delete (inclusive)",
        "sheetName": "Optional: The name of the sheet to delete from"
    },
    schema={
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
# @validated("delete_rows")
def delete_rows_handler(current_user, data):
    return common_handler(delete_rows, 'spreadsheetId', 'startRow', 'endRow', sheetName=None)(current_user, data)

@vop(
    path="/google/integrations/update_rows",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleUpdateRows",
    description="Updates specified rows in a Google Sheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to update. The first item in each array should be the row number to update. Example to update rows 3 and 8: [[3, 'something'],[8, 'new value 1', 'new value 2']]",
        "sheetName": "Optional: The name of the sheet to update"
    },
    schema={
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
# @validated("update_rows")
def update_rows_handler(current_user, data):
    return common_handler(update_rows, 'spreadsheetId', 'rowsData', sheetName=None)(current_user, data)

@vop(
    path="/google/integrations/create_spreadsheet",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleCreateSpreadsheet",
    description="Creates a new Google Sheets spreadsheet.",
    params={
        "title": "The title of the new spreadsheet"
    },
    schema={
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
# @validated("create_spreadsheet")
def create_spreadsheet_handler(current_user, data):
    return common_handler(create_spreadsheet, 'title')(current_user, data)

@vop(
    path="/google/integrations/duplicate_sheet",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleDuplicateSheet",
    description="Duplicates a sheet within a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to duplicate",
        "newSheetName": "The name for the new duplicated sheet"
    },
    schema={
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
# @validated("duplicate_sheet")
def duplicate_sheet_handler(current_user, data):
    return common_handler(duplicate_sheet, 'spreadsheetId', 'sheetId', 'newSheetName')(current_user, data)

@vop(
    path="/google/integrations/rename_sheet",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleRenameSheet",
    description="Renames a sheet in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet to rename",
        "newName": "The new name for the sheet"
    },
    schema={
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
# @validated("rename_sheet")
def rename_sheet_handler(current_user, data):
    return common_handler(rename_sheet, 'spreadsheetId', 'sheetId', 'newName')(current_user, data)

@vop(
    path="/google/integrations/clear_range",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleClearRange",
    description="Clears a range of cells in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to clear"
    },
    schema={
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
# @validated("clear_range")
def clear_range_handler(current_user, data):
    return common_handler(clear_range, 'spreadsheetId', 'rangeName')(current_user, data)

@vop(
    path="/google/integrations/apply_formatting",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleApplyFormatting",
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
    schema={
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
# @validated("apply_formatting")
def apply_formatting_handler(current_user, data):
    return common_handler(apply_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'formatJson')(current_user, data)

@vop(
    path="/google/integrations/add_chart",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleAddChart",
    description="Adds a chart to a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "sheetId": "The ID of the sheet",
        "chartSpec": "The chart specification as a JSON object"
    },
    schema={
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
# @validated("add_chart")
def add_chart_handler(current_user, data):
    return common_handler(add_chart, 'spreadsheetId', 'sheetId', 'chartSpec')(current_user, data)

@vop(
    path="/google/integrations/get_cell_formulas",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_read"],
    name="googleGetCellFormulas",
    description="Gets cell formulas for a range in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "rangeName": "The A1 notation of the range to get formulas from"
    },
    schema={
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
# @validated("get_cell_formulas")
def get_cell_formulas_handler(current_user, data):
    return common_handler(get_cell_formulas, 'spreadsheetId', 'rangeName')(current_user, data)

@vop(
    path="/google/integrations/find_replace",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleFindReplace",
    description="Finds and replaces text in a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "find": "The text to find",
        "replace": "The text to replace with",
        "sheetId": "Optional: The ID of the sheet to perform find/replace on"
    },
    schema={
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
                "description": "The ID of the sheet to perform find/replace on. Default is 0"
            }
        },
        "required": ["spreadsheetId", "find", "replace"]
    }
)
# @validated("find_replace")
def find_replace_handler(current_user, data):
    return common_handler(find_replace, 'spreadsheetId', 'find', 'replace', sheetId='sheetId')(current_user, data)

@vop(
    path="/google/integrations/sort_range",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleSortRange",
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
    schema={
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
# @validated("sort_range")
def sort_range_handler(current_user, data):
    return common_handler(sort_range, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'sortOrder')(current_user, data)

@vop(
    path="/google/integrations/apply_conditional_formatting",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_write"],
    name="googleApplyConditionalFormatting",
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
    schema={
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
# @validated("apply_conditional_formatting")
def apply_conditional_formatting_handler(current_user, data):
    return common_handler(apply_conditional_formatting, 'spreadsheetId', 'sheetId', 'startRow', 'endRow', 'startCol', 'endCol', 'condition', 'format')(current_user, data)


@vop(
    path="/google/integrations/execute_query",
    type="integration",
    tags=["default", "integration", "google_sheets", "google_sheets_read"],
    name="googleExecuteQuery",
    description="Executes a SQL-like query on a Google Sheets spreadsheet.",
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "query": "The SQL-like query to execute e.g., a == 1 and b < 34",
        "sheetName": "(Optional) The name of the sheet to query"
    },
    schema={
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
# @validated("execute_query")
def execute_query_handler(current_user, data):
    return common_handler(execute_query, 'spreadsheetId', 'query', sheetName='sheetName')(current_user, data)

@vop(
    path="/google/integrations/create_new_document",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleCreateNewDocument",
    description="Creates a new Google Docs document.",
    params={
        "title": "The title of the new document"
    },
    schema={
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
# @validated("create_new_document")
def create_new_document_handler(current_user, data):
    return common_handler(create_new_document, 'title')(current_user, data)

@vop(
    path="/google/integrations/get_document_contents",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_read"],
    name="googleGetDocumentContents",
    description="Retrieves the contents of a Google Docs document.",
    params={
        "documentId": "The ID of the document"
    },
    schema={
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
# @validated("get_document_contents")
def get_document_contents_handler(current_user, data):
    return common_handler(get_document_contents, 'documentId')(current_user, data)

@vop(
    path="/google/integrations/insert_text",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleInsertText",
    description="Inserts text at a specific location in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "text": "The text to insert",
        "index": "The index at which to insert the text"
    },
    schema={
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
# @validated("insert_text")
def insert_text_handler(current_user, data):
    return common_handler(insert_text, 'documentId', 'text', 'index')(current_user, data)

@vop(
    path="/google/integrations/append_text",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleAppendText",
    description="Appends text to the end of a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "text": "The text to append"
    },
    schema={
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
# @validated("append_text")
def append_text_handler(current_user, data):
    return common_handler(append_text, 'documentId', 'text')(current_user, data)

@vop(
    path="/google/integrations/replace_text",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleReplaceText",
    description="Replaces all occurrences of text in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "oldText": "The text to be replaced",
        "newText": "The text to replace with"
    },
    schema={
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
# @validated("replace_text")
def replace_text_handler(current_user, data):
    return common_handler(replace_text, 'documentId', 'oldText', 'newText')(current_user, data)

@vop(
    path="/google/integrations/create_document_outline",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleCreateDocumentOutline",
    description="Creates an outline in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "outlineItems": "An array of objects with 'start' and 'end' indices for each outline item"
    },
    schema={
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
# @validated("create_document_outline")
def create_document_outline_handler(current_user, data):
    return common_handler(create_document_outline, 'documentId', 'outlineItems')(current_user, data)

@vop(
    path="/google/integrations/export_document",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_read"],
    name="googleExportDocument",
    description="Exports a Google Docs document to a specified format.",
    params={
        "documentId": "The ID of the document",
        "mimeType": "The MIME type of the format to export to"
    },
    schema={
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
# @validated("export_document")
def export_document_handler(current_user, data):
    return common_handler(export_document, 'documentId', 'mimeType')(current_user, data)

@vop(
    path="/google/integrations/share_document",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_write"],
    name="googleShareDocument",
    description="Shares a Google Docs document with another user.",
    params={
        "documentId": "The ID of the document",
        "email": "The email address of the user to share with",
        "role": "The role to grant to the user (e.g., 'writer', 'reader')"
    },
    schema={
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
# @validated("share_document")
def share_document_handler(current_user, data):
    return common_handler(share_document, 'documentId', 'email', 'role')(current_user, data)

@vop(
    path="/google/integrations/find_text_indices",
    type="integration",
    tags=["default", "integration", "google_docs", "google_docs_read"],
    name="googleFindTextIndices",
    description="Finds the indices of a specific text in a Google Docs document.",
    params={
        "documentId": "The ID of the document",
        "searchText": "The text to search for"
    },
    schema={
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
# @validated("find_text_indices")
def find_text_indices_handler(current_user, data):
    return common_handler(find_text_indices, 'documentId', 'searchText')(current_user, data)

@vop(
    path="/google/integrations/get_events_between_dates",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleGetEventsBetweenDates",
    description="Retrieves events from Google Calendar between two specified dates.",
    params={
        "startDate": "The start date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)",
        "endDate": "The end date in ISO 8601 format (e.g., 2024-12-20T23:59:59Z)",
        "includeDescription": "Optional. Include event description (default: false)",
        "includeAttendees": "Optional. Include event attendees (default: false)",
        "includeLocation": "Optional. Include event location (default: false)"
    },
    schema={
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
# @validated("get_events_between_dates")
def get_events_between_dates_handler(current_user, data):
    return common_handler(get_events_between_dates, 'startDate', 'endDate', includeDescription=False, includeAttendees=False, includeLocation=False)(current_user, data)

@vop(
    path="/google/integrations/create_event",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleCreateEvent",
    description="Creates a new event in Google Calendar.",
    params={
        "title": "The title of the event",
        "startTime": "The start time of the event in ISO 8601 format",
        "endTime": "The end time of the event in ISO 8601 format",
        "description": "Optional: The description of the event",
        "location": "Optional: The physical location for in-person meetings",
        "attendees": "Optional: List of attendees' email addresses",
        "calendarId": "Optional: The ID of the calendar to create the event on (default: 'primary')",
        "conferenceData": "Optional: Information for virtual meeting (e.g., Google Meet)"
    },
    schema={
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
            "description": {
                "type": "string",
                "description": "Optional: The description of the event"
            },
            "location": {
                "type": "string",
                "description": "Optional: The physical location for in-person meetings"
            },
            "attendees": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "Optional: List of attendees' email addresses"
            },
            "calendarId": {
                "type": "string",
                "description": "Optional: The ID of the calendar to create the event on (default: 'primary')"
            },
            "conferenceData": {
                "type": "object",
                "description": "Optional: Information for virtual meeting (e.g., Google Meet)"
            }
        },
        "required": ["title", "startTime", "endTime"]
    }
)
# @validated("create_event")
def create_event_handler(current_user, data):
    return common_handler(create_event, 'title', 'startTime', 'endTime', 'description', 
                         location='location', attendees='attendees', calendar_id='calendarId', 
                         conference_data='conferenceData')(current_user, data)

@vop(
    path="/google/integrations/update_event",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleUpdateEvent",
    description="Updates an existing event in Google Calendar.",
    params={
        "eventId": "The ID of the event to update",
        "updatedFields": "A dictionary of fields to update and their new values"
    },
    schema={
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
# @validated("update_event")
def update_event_handler(current_user, data):
    return common_handler(update_event, 'eventId', 'updatedFields')(current_user, data)

@vop(
    path="/google/integrations/delete_event",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleDeleteEvent",
    description="Deletes an event from Google Calendar.",
    params={
        "eventId": "The ID of the event to delete"
    },
    schema={
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
# @validated("delete_event")
def delete_event_handler(current_user, data):
    return common_handler(delete_event, 'eventId')(current_user, data)

@vop(
    path="/google/integrations/get_event_details",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleGetEventDetails",
    description="Retrieves details of a specific event from Google Calendar.",
    params={
        "eventId": "The ID of the event to retrieve"
    },
    schema={
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
# @validated("get_event_details")
def get_event_details_handler(current_user, data):
    return common_handler(get_event_details, 'eventId')(current_user, data)

@vop(
    path="/google/integrations/get_events_for_date",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleGetEventsForDate",
    description="Retrieves events from Google Calendar for a specific date.",
    params={
        "date": "The date in ISO 8601 format (YYYY-MM-DD)",
        "includeDescription": "Optional. Include event description (default: false)",
        "includeAttendees": "Optional. Include event attendees (default: false)",
        "includeLocation": "Optional. Include event location (default: false)"
    },
    schema={
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
# @validated("get_events_for_date")
def get_events_for_date_handler(current_user, data):
    return common_handler(get_events_for_date, 'date', includeDescription=False, includeAttendees=False, includeLocation=False)(current_user, data)

@vop(
    path="/google/integrations/get_free_time_slots",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleGetFreeTimeSlots",
    description="Finds free time slots in Google Calendar between two dates.",
    params={
        "startDate": "The start date in ISO 8601 format",
        "endDate": "The end date in ISO 8601 format",
        "duration": "The minimum duration of free time slots in minutes",
        'userTimeZone': "Optional. The time zone of the user (default: 'America/Chicago')",
        'includeWeekends': "Optional. Whether to include weekends (default: false)",
        'allowedTimeWindows': "Optional. List of time windows in format ['HH:MM-HH:MM']",
        'excludeDates': "Optional. List of dates to exclude in ISO 8601 format"
    },
    schema={
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
                'type': "string",
                'description': 'Optional. The time zone of the user (default: \'America/Chicago\')',
                "default": "America/Chicago"
            },
            'includeWeekends': {
                'type': "boolean",
                'description': 'Optional. Whether to include weekends',
                "default": False
            },
            'allowedTimeWindows': {
                'type': "array",
                'items': {
                    'type': "string",
                    'pattern': "^([0-1][0-9]|2[0-3]):[0-5][0-9]-([0-1][0-9]|2[0-3]):[0-5][0-9]$"
                },
                'description': 'Optional. List of time windows in format ["HH:MM-HH:MM"]'
            },
            'excludeDates': {
                'type': "array",
                'items': {
                    'type': "string",
                    'format': "date"
                },
                'description': 'Optional. List of dates to exclude in ISO 8601 format'
            }
        },
        'required': ['startDate', 'endDate', 'duration']
    }
)
# @validated("get_free_time_slots")
def get_free_time_slots_handler(current_user, data):
    return common_handler(get_free_time_slots, 'startDate', 'endDate', 'duration', userTimeZone="America/Chicago",
                          includeWeekends=False, allowedTimeWindows=None, excludeDates=None)(current_user, data)

@vop(
    path="/google/integrations/check_event_conflicts",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleCheckEventConflicts",
    description="Checks for scheduling conflicts within a time window across one or more calendars.",
    params={
        "proposedStartTime": "The start time of the proposed event in ISO 8601 format",
        "proposedEndTime": "The end time of the proposed event in ISO 8601 format",
        "returnConflictingEvents": "Optional: Whether to include details of conflicting events",
        "calendarIds": "Optional: List of calendar IDs to check",
        "checkAllCalendars": "Optional: If true, checks all calendars the user has access to"
    },
    schema={
        "type": "object",
        "properties": {
            "proposedStartTime": {
                "type": "string",
                "description": "The start time of the proposed event in ISO 8601 format"
            },
            "proposedEndTime": {
                "type": "string",
                "description": "The end time of the proposed event in ISO 8601 format"
            },
            "returnConflictingEvents": {
                "type": "boolean",
                "description": "Optional: Whether to include details of conflicting events"
            },
            "calendarIds": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "Optional: List of calendar IDs to check"
            },
            "checkAllCalendars": {
                "type": "boolean",
                "description": "Optional: If true, checks all calendars the user has access to"
            }
        },
        "required": ["proposedStartTime", "proposedEndTime"]
    }
)
# @validated("check_event_conflicts")
def check_event_conflicts_handler(current_user, data):
    return common_handler(check_event_conflicts, 'proposedStartTime', 'proposedEndTime', 
                         return_conflicting_events='returnConflictingEvents',
                         calendar_ids='calendarIds', check_all_calendars='checkAllCalendars')(current_user, data)

@vop(
    path="/google/integrations/list_files",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleListFiles",
    description="Lists files in a specific folder or root directory of Google Drive.",
    params={
        "folderId": "The ID of the folder to list files from (optional)"
    },
    schema={
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
# @validated("list_files")
def list_files_handler(current_user, data):
    return common_handler(list_files, 'folderId')(current_user, data)

@vop(
    path="/google/integrations/search_files",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleSearchFiles",
    description="Searches for files in Google Drive based on a query. You should know that \"name contains '<query>'\" is added automatically to the query.",
    params={
        "query": "The search query string"
    },
    schema={
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
# @validated("search_files")
def search_files_handler(current_user, data):
    return common_handler(search_files, 'query')(current_user, data)

@vop(
    path="/google/integrations/get_file_metadata",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleGetFileMetadata",
    description="Retrieves metadata for a specific file in Google Drive.",
    params={
        "fileId": "The ID of the file"
    },
    schema={
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
# @validated("get_file_metadata")
def get_file_metadata_handler(current_user, data):
    return common_handler(get_file_metadata, 'fileId')(current_user, data)

@vop(
    path="/google/integrations/get_file_content",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleGetFileContent",
    description="Gets the content of a file in Google Drive as text.",
    params={
        "fileId": "The ID of the file"
    },
    schema={
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
# @validated("get_file_content")
def get_file_content_handler(current_user, data):
    return common_handler(get_file_content, 'fileId')(current_user, data)

@vop(
    path="/google/integrations/create_file",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleCreateFile",
    description="Creates a new file in Google Drive with the given content.",
    params={
        "fileName": "The name of the file to create",
        "content": "The content of the file",
        "mimeType": "The MIME type of the file (optional, defaults to 'text/plain')"
    },
    schema={
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
# @validated("create_file")
def create_file_handler(current_user, data):
    return common_handler(create_file, 'fileName', 'content', 'mimeType')(current_user, data)

@vop(
    path="/google/integrations/get_download_link",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleGetDownloadLink",
    description="Gets the download link for a file in Google Drive.",
    params={
        "fileId": "The ID of the file"
    },
    schema={
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
# @validated("get_download_link")
def get_download_link_handler(current_user, data):
    return common_handler(get_download_link, 'fileId')(current_user, data)

@vop(
    path="/google/integrations/create_shared_link",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleCreateSharedLink",
    description="Creates a shared link for a file in Google Drive with view or edit permissions.",
    params={
        "fileId": "The ID of the file",
        "permission": "The permission level ('view' or 'edit')"
    },
    schema={
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
# @validated("create_shared_link")
def create_shared_link_handler(current_user, data):
    return common_handler(create_shared_link, 'fileId', 'permission')(current_user, data)

@vop(
    path="/google/integrations/share_file",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleShareFile",
    description="Shares a file in Google Drive with multiple email addresses.",
    params={
        "fileId": "The ID of the file",
        "emails": "List of email addresses to share the file with",
        "role": "The role to assign ('reader', 'commenter', or 'writer')"
    },
    schema={
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
# @validated("share_file")
def share_file_handler(current_user, data):
    return common_handler(share_file, 'fileId', 'emails', 'role')(current_user, data)

@vop(
    path="/google/integrations/convert_file",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleConvertFile",
    description="Converts a file in Google Drive to a specified format and returns its download link.",
    params={
        "fileId": "The ID of the file to convert",
        "targetMimeType": "The target MIME type for conversion"
    },
    schema={
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
# @validated("convert_file")
def convert_file_handler(current_user, data):
    return common_handler(convert_file, 'fileId', 'targetMimeType')(current_user, data)

@vop(
    path="/google/integrations/list_folders",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleListFolders",
    description="Lists folders in Google Drive, optionally within a specific parent folder.",
    params={
        "parentFolderId": "The ID of the parent folder (optional)"
    },
    schema={
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
# @validated("list_folders")
def list_folders_handler(current_user, data):
    return common_handler(list_folders, 'parentFolderId')(current_user, data)

@vop(
    path="/google/integrations/move_item",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleMoveItem",
    description="Moves a file or folder to a specified destination folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to move",
        "destinationFolderId": "The ID of the destination folder"
    },
    schema={
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
# @validated("move_item")
def move_item_handler(current_user, data):
    return common_handler(move_item, 'itemId', 'destinationFolderId')(current_user, data)

@vop(
    path="/google/integrations/copy_item",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleCopyItem",
    description="Copies a file or folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to copy",
        "newName": "The name for the copied item (optional)"
    },
    schema={
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
# @validated("copy_item")
def copy_item_handler(current_user, data):
    return common_handler(copy_item, 'itemId', 'newName')(current_user, data)

@vop(
    path="/google/integrations/rename_item",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleRenameItem",
    description="Renames a file or folder in Google Drive.",
    params={
        "itemId": "The ID of the file or folder to rename",
        "newName": "The new name for the item"
    },
    schema={
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
# @validated("rename_item")
def rename_item_handler(current_user, data):
    return common_handler(rename_item, 'itemId', 'newName')(current_user, data)



@vop(
    path="/google/integrations/get_file_revisions",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleGetFileRevisions",
    description="Gets the revision history of a file in Google Drive.",
    params={
        "fileId": "The ID of the file to get revisions for"
    },
    schema={
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
# @validated("get_file_revisions")
def get_file_revisions_handler(current_user, data):
    return common_handler(get_file_revisions, 'fileId')(current_user, data)


@vop(
    path="/google/integrations/create_folder",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleCreateFolder",
    description="Creates a new folder in Google Drive.",
    params={
        "folderName": "The name of the new folder",
        "parentId": "The ID of the parent folder (optional)"
    },
    schema={
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
# @validated("create_folder")
def create_folder_handler(current_user, data):
    return common_handler(create_folder, 'folderName', 'parentId')(current_user, data)



@vop(
    path="/google/integrations/delete_item_permanently",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_write"],
    name="googleDeleteItemPermanently",
    description="Permanently deletes a file or folder from Google Drive.",
    params={
        "itemId": "The ID of the file or folder to delete"
    },
    schema={
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
# @validated("delete_item_permanently")
def delete_item_permanently_handler(current_user, data):
    return common_handler(delete_item_permanently, 'itemId')(current_user, data)


@vop(
    path="/google/integrations/get_root_folder_ids",
    type="integration",
    tags=["default", "integration", "google_drive", "google_drive_read"],
    name="googleGetRootFolderIds",
    description="Retrieves the IDs of root-level folders in Google Drive.",
    params={
    },
    schema={
        "type": "object",
        "properties": {}
    }
)
# @validated("get_root_folder_ids")
def get_root_folder_ids_handler(current_user, data):
    return common_handler(get_root_folder_ids)(current_user, data)


@vop(
    path="/google/integrations/create_form",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleCreateForm",
    description="Creates a new Google Form.",
    params={
        "title": "The title of the new form",
        "description": "Optional description for the form"
    },
    schema={
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
# @validated("create_form")
def create_form_handler(current_user, data):
    return common_handler(create_form, 'title', description="")(current_user, data)

@vop(
    path="/google/integrations/get_form_details",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_read"],
    name="googleGetFormDetails",
    description="Retrieves details of a specific Google Form.",
    params={
        "formId": "The ID of the form to retrieve"
    },
    schema={
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
# @validated("get_form_details")
def get_form_details_handler(current_user, data):
    return common_handler(get_form_details, 'formId')(current_user, data)

@vop(
    path="/google/integrations/add_question",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleAddQuestion",
    description="Adds a new question to a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionType": "The type of question (e.g., 'TEXT', 'MULTIPLE_CHOICE', 'CHECKBOX')",
        "title": "The title of the question",
        "required": "Whether the question is required (default: false)",
        "options": "List of options for multiple choice or checkbox questions (optional)"
    },
    schema={
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
# @validated("add_question")
def add_question_handler(current_user, data):
    return common_handler(add_question, 'formId', 'questionType', 'title', required=False, options=None)(current_user, data)

@vop(
    path="/google/integrations/update_question",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleUpdateQuestion",
    description="Updates an existing question in a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionId": "The ID of the question to update",
        "title": "The new title of the question (optional)",
        "required": "Whether the question is required (optional)",
        "options": "New list of options for multiple choice or checkbox questions (optional)"
    },
    schema={
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
# @validated("update_question")
def update_question_handler(current_user, data):
    return common_handler(update_question, 'formId', 'questionId', title=None, required=None, options=None)(current_user, data)

@vop(
    path="/google/integrations/delete_question",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleDeleteQuestion",
    description="Deletes a question from a Google Form.",
    params={
        "formId": "The ID of the form",
        "questionId": "The ID of the question to delete"
    },
    schema={
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
# @validated("delete_question")
def delete_question_handler(current_user, data):
    return common_handler(delete_question, 'formId', 'questionId')(current_user, data)

@vop(
    path="/google/integrations/get_responses",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_read"],
    name="googleGetResponses",
    description="Retrieves all responses for a Google Form.",
    params={
        "formId": "The ID of the form"
    },
    schema={
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
# @validated("get_responses")
def get_responses_handler(current_user, data):
    return common_handler(get_responses, 'formId')(current_user, data)

@vop(
    path="/google/integrations/get_response",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_read"],
    name="googleGetResponse",
    description="Retrieves a specific response from a Google Form.",
    params={
        "formId": "The ID of the form",
        "responseId": "The ID of the response to retrieve"
    },
    schema={
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
# @validated("get_response")
def get_response_handler(current_user, data):
    return common_handler(get_response, 'formId', 'responseId')(current_user, data)

@vop(
    path="/google/integrations/set_form_settings",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleSetFormSettings",
    description="Updates the settings of a Google Form.",
    params={
        "formId": "The ID of the form",
        "settings": "A dictionary of settings to update"
    },
    schema={
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
# @validated("set_form_settings")
def set_form_settings_handler(current_user, data):
    return common_handler(set_form_settings, 'formId', 'settings')(current_user, data)

@vop(
    path="/google/integrations/get_form_link",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_read"],
    name="googleGetFormLink",
    description="Retrieves the public link for a Google Form.",
    params={
        "formId": "The ID of the form"
    },
    schema={
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
# @validated("get_form_link")
def get_form_link_handler(current_user, data):
    return common_handler(get_form_link, 'formId')(current_user, data)

@vop(
    path="/google/integrations/update_form_info",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_write"],
    name="googleUpdateFormInfo",
    description="Updates the title and/or description of a Google Form.",
    params={
        "formId": "The ID of the form",
        "title": "The new title for the form (optional)",
        "description": "The new description for the form (optional)"
    },
    schema={
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
# @validated("update_form_info")
def update_form_info_handler(current_user, data):
    return common_handler(update_form_info, 'formId', title=None, description=None)(current_user, data)

@vop(
    path="/google/integrations/list_user_forms",
    type="integration",
    tags=["default", "integration", "google_forms", "google_forms_read"],
    name="googleListUserForms",
    description="Lists all forms owned by the current user.",
    params={},
    schema={
        "type": "object",
        "properties": {}
    }
)
# @validated("list_user_forms")
def list_user_forms_handler(current_user, data):
    return common_handler(list_user_forms)(current_user, data)

@vop(
    path="/google/integrations/compose_and_send_email",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_write"],
    name="googleComposeAndSendEmail",
    description="Composes and sends an email, with an option to schedule for future.",
    params={
        "to": "Recipient email address(es) as a string, comma-separated for multiple recipients",
        "subject": "Email subject",
        "body": "Email body content",
        "cc": "Optional: CC recipient(s) email address(es)",
        "bcc": "Optional: BCC recipient(s) email address(es)",
        "scheduleTime": "Optional: ISO format datetime string for scheduled sending"
    },
    schema={
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
# @validated("compose_and_send_email")
def compose_and_send_email_handler(current_user, data):
    return common_handler(compose_and_send_email, 'to', 'subject', 'body', cc=None, bcc=None, schedule_time=None)(current_user, data)

@vop(
    path="/google/integrations/compose_email_draft",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_write"],
    name="googleComposeEmailDraft",
    description="Composes an email draft.",
    params={
        "to": "Recipient email address(es) as a string, comma-separated for multiple recipients",
        "subject": "Email subject",
        "body": "Email body content",
        "cc": "Optional: CC recipient(s) email address(es)",
        "bcc": "Optional: BCC recipient(s) email address(es)"
    },
    schema={
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
# @validated("compose_email_draft")
def compose_email_draft_handler(current_user, data):
    return common_handler(compose_email_draft, 'to', 'subject', 'body', cc=None, bcc=None)(current_user, data)

@vop(
    path="/google/integrations/get_messages_from_date",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleGetMessagesFromDate",
    description="Gets messages from a specific start date (optional label).",
    params={
        "n": "Number of messages to retrieve",
        "startDate": "Start date in YYYY-MM-DD format",
        "label": "Optional: Label to filter messages"
    },
    schema={
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
# @validated("get_messages_from_date")
def get_messages_from_date_handler(current_user, data):
    return common_handler(get_messages_from_date, 'n', 'start_date', label=None)(current_user, data)

@vop(
    path="/google/integrations/get_recent_messages",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleGetRecentMessages",
    description="Gets the N most recent messages (optional label).",
    params={
        "n": "Number of messages to retrieve (default 25)",
        "label": "Optional: Label to filter messages"
    },
    schema={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of messages to retrieve. Default is 25"
            },
            "label": {
                "type": "string",
                "description": "Optional: Label to filter messages"
            }
        }
    }
)
# @validated("get_recent_messages")
def get_recent_messages_handler(current_user, data):
    return common_handler(get_recent_messages, n=25, label=None)(current_user, data)


@vop(
    path="/google/integrations/search_messages",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleSearchMessages",
    description="Searches for messages using the Gmail search language.",
    params={
        "query": "Search query string using Gmail search language"
    },
    schema={
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
# @validated("search_messages")
def search_messages_handler(current_user, data):
    return common_handler(search_messages, 'query')(current_user, data)

@vop(
    path="/google/integrations/get_attachment_links",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleGetAttachmentLinks",
    description="Gets links to download attachments for a specific email.",
    params={
        "messageId": "ID of the email message"
    },
    schema={
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
# @validated("get_attachment_links")
def get_attachment_links_handler(current_user, data):
    return common_handler(get_attachment_links, 'message_id')(current_user, data)

@vop(
    path="/google/integrations/get_attachment_content",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleGetAttachmentContent",
    description="Gets the content of a specific attachment.",
    params={
        "messageId": "ID of the email message",
        "attachmentId": "ID of the attachment"
    },
    schema={
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
# @validated("get_attachment_content")
def get_attachment_content_handler(current_user, data):
    return common_handler(get_attachment_content, 'message_id', 'attachment_id')(current_user, data)

@vop(
    path="/google/integrations/create_filter",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_write"],
    name="googleCreateFilter",
    description="Creates a new email filter.",
    params={
        "criteria": "Filter criteria as a dictionary",
        "action": "Action to take when filter criteria are met, as a dictionary"
    },
    schema={
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
# @validated("create_filter")
def create_filter_handler(current_user, data):
    return common_handler(create_filter, 'criteria', 'action')(current_user, data)

@vop(
    path="/google/integrations/create_label",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_write"],
    name="googleCreateLabel",
    description="Creates a new label.",
    params={
        "name": "Name of the new label"
    },
    schema={
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
# @validated("create_label")
def create_label_handler(current_user, data):
    return common_handler(create_label, 'name')(current_user, data)

@vop(
    path="/google/integrations/create_auto_filter_label_rule",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_write"],
    name="googleCreateAutoFilterLabelRule",
    description="Creates an auto-filter and label rule.",
    params={
        "criteria": "Filter criteria as a dictionary",
        "labelName": "Name of the label to apply"
    },
    schema={
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
# @validated("create_auto_filter_label_rule")
def create_auto_filter_label_rule_handler(current_user, data):
    return common_handler(create_auto_filter_label_rule, 'criteria', 'label_name')(current_user, data)

@vop(
    path="/google/integrations/get_message_details",
    type="integration",
    tags=["default", "integration", "google_gmail", "google_gmail_read"],
    name="googleGetMessageDetails",
    description="Gets detailed information, such as body, bcc, sent date, etc. for one or more Gmail messages.",
    params={
        "message_id": "ID of the message to retrieve details for",
        "fields": "Optional: List of fields to include in the response. Default is (id, sender, subject, labels, date). Full list is (id, threadId, historyId, sizeEstimate, raw, payload, mimeType, attachments, sender, subject, labels, date, snippet, body, cc, bcc, deliveredTo, receivedTime, sentTime)"
    },
    schema={
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
# @validated("get_message_details")
def get_message_details_handler(current_user, data):
    return common_handler(get_message_details, message_id=None, fields=None)(current_user, data)

@vop(
    path="/google/integrations/search_contacts",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_read"],
    name="googleSearchContacts",
    description="Searches the user's Google Contacts.",
    params={
        "query": "Search query string",
        "page_size": "Optional: Number of results to return (default 10)"
    },
    schema={
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
# @validated("search_contacts")
def search_contacts_handler(current_user, data):
    return common_handler(search_contacts, query=None, page_size=10)(current_user, data)

@vop(
    path="/google/integrations/get_contact_details",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_read"],
    name="googleGetContactDetails",
    description="Gets details for a specific contact.",
    params={
        "resource_name": "Resource name of the contact"
    },
    schema={
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
# @validated("get_contact_details")
def get_contact_details_handler(current_user, data):
    return common_handler(get_contact_details, resource_name=None)(current_user, data)

@vop(
    path="/google/integrations/create_contact",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleCreateContact",
    description="Creates a new contact.",
    params={
        "contact_info": "Contact information"
    },
    schema={
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
# @validated("create_contact")
def create_contact_handler(current_user, data):
    return common_handler(create_contact, contact_info=None)(current_user, data)

@vop(
    path="/google/integrations/update_contact",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleUpdateContact",
    description="Updates an existing contact.",
    params={
        "resource_name": "Resource name of the contact",
        "contact_info": "Updated contact information"
    },
    schema={
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
# @validated("update_contact")
def update_contact_handler(current_user, data):
    return common_handler(update_contact, resource_name=None, contact_info=None)(current_user, data)

@vop(
    path="/google/integrations/delete_contact",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleDeleteContact",
    description="Deletes a contact.",
    params={
        "resource_name": "Resource name of the contact to delete"
    },
    schema={
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
# @validated("delete_contact")
def delete_contact_handler(current_user, data):
    return common_handler(delete_contact, resource_name=None)(current_user, data)

@vop(
    path="/google/integrations/list_contact_groups",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_read"],
    name="googleListContactGroups",
    description="Lists all contact groups.",
    params={},
    schema={
        "type": "object",
        "properties": {}
    }
)
# @validated("list_contact_groups")
def list_contact_groups_handler(current_user, data):
    return common_handler(list_contact_groups)(current_user, data)

@vop(
    path="/google/integrations/create_contact_group",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleCreateContactGroup",
    description="Creates a new contact group.",
    params={
        "group_name": "Name of the new contact group"
    },
    schema={
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
# @validated("create_contact_group")
def create_contact_group_handler(current_user, data):
    return common_handler(create_contact_group, group_name=None)(current_user, data)

@vop(
    path="/google/integrations/update_contact_group",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleUpdateContactGroup",
    description="Updates an existing contact group.",
    params={
        "resource_name": "Resource name of the contact group",
        "new_name": "New name for the contact group"
    },
    schema={
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
# @validated("update_contact_group")
def update_contact_group_handler(current_user, data):
    return common_handler(update_contact_group, resource_name=None, new_name=None)(current_user, data)

@vop(
    path="/google/integrations/delete_contact_group",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleDeleteContactGroup",
    description="Deletes a contact group.",
    params={
        "resource_name": "Resource name of the contact group to delete"
    },
    schema={
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
# @validated("delete_contact_group")
def delete_contact_group_handler(current_user, data):
    return common_handler(delete_contact_group, resource_name=None)(current_user, data)

@vop(
    path="/google/integrations/add_contacts_to_group",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleAddContactsToGroup",
    description="Adds contacts to a group.",
    params={
        "group_resource_name": "Resource name of the contact group",
        "contact_resource_names": "List of resource names of contacts to add"
    },
    schema={
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
# @validated("add_contacts_to_group")
def add_contacts_to_group_handler(current_user, data):
    return common_handler(add_contacts_to_group, group_resource_name=None, contact_resource_names=None)(current_user, data)

@vop(
    path="/google/integrations/remove_contacts_from_group",
    type="integration",
    tags=["default", "integration", "google_contacts", "google_contacts_write"],
    name="googleRemoveContactsFromGroup",
    description="Removes contacts from a group.",
    params={
        "group_resource_name": "Resource name of the contact group",
        "contact_resource_names": "List of resource names of contacts to remove"
    },
    schema={
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
# @validated("remove_contacts_from_group")
def remove_contacts_from_group_handler(current_user, data):
    return common_handler(remove_contacts_from_group, group_resource_name=None, contact_resource_names=None)(current_user, data)

@vop(
    path="/google/integrations/list_calendars",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleListCalendars",
    description="Lists all calendars the user has access to in Google Calendar.",
    params={},
    schema={
        "type": "object",
        "properties": {}
    }
)
def list_calendars_handler(current_user, data):
    return common_handler(list_calendars)(current_user, data)

@vop(
    path="/google/integrations/create_calendar",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleCreateCalendar",
    description="Creates a new calendar in Google Calendar.",
    params={
        "name": "Name/summary of the calendar",
        "description": "Optional description for the calendar",
        "timezone": "Optional timezone for the calendar"
    },
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name/summary of the calendar"
            },
            "description": {
                "type": "string",
                "description": "Optional description for the calendar"
            },
            "timezone": {
                "type": "string",
                "description": "Optional timezone for the calendar"
            }
        },
        "required": ["name"]
    }
)
def create_calendar_handler(current_user, data):
    return common_handler(create_calendar, 'name', description=None, timezone=None)(current_user, data)

@vop(
    path="/google/integrations/delete_calendar",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleDeleteCalendar",
    description="Deletes a calendar from Google Calendar. Cannot delete primary calendar.",
    params={
        "calendarId": "The ID of the calendar to delete"
    },
    schema={
        "type": "object",
        "properties": {
            "calendarId": {
                "type": "string",
                "description": "The ID of the calendar to delete"
            }
        },
        "required": ["calendarId"]
    }
)
def delete_calendar_handler(current_user, data):
    return common_handler(delete_calendar, 'calendarId')(current_user, data)

@vop(
    path="/google/integrations/update_calendar_permissions",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleUpdateCalendarPermissions",
    description="Shares a calendar with another user by setting permissions.",
    params={
        "calendarId": "The ID of the calendar to share",
        "email": "Email address of the user to share with",
        "role": "Permission role ('none', 'freeBusyReader', 'reader', 'writer', 'owner')",
        "sendNotification": "Whether to send notification email",
        "notificationMessage": "Optional custom message for notification"
    },
    schema={
        "type": "object",
        "properties": {
            "calendarId": {
                "type": "string",
                "description": "The ID of the calendar to share"
            },
            "email": {
                "type": "string",
                "description": "Email address of the user to share with"
            },
            "role": {
                "type": "string",
                "enum": ["none", "freeBusyReader", "reader", "writer", "owner"],
                "description": "Permission role",
                "default": "reader"
            },
            "sendNotification": {
                "type": "boolean",
                "description": "Whether to send notification email",
                "default": False
            },
            "notificationMessage": {
                "type": "string",
                "description": "Optional custom message for notification"
            }
        },
        "required": ["calendarId", "email"]
    }
)
def update_calendar_permissions_handler(current_user, data):
    return common_handler(
        update_calendar_permissions, 
        'calendarId', 'email', 
        role='reader', 
        send_notification=False, 
        notification_message=None
    )(current_user, data)

@vop(
    path="/google/integrations/create_recurring_event",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleCreateRecurringEvent",
    description="Creates a recurring event in Google Calendar.",
    params={
        "title": "The title of the event",
        "startTime": "The start time of the first occurrence in ISO 8601 format",
        "endTime": "The end time of the first occurrence in ISO 8601 format",
        "description": "Optional: The description of the event",
        "location": "Optional: The physical location for in-person meetings",
        "attendees": "Optional: List of attendees' email addresses",
        "recurrencePattern": "Optional: List of RRULE strings (e.g., ['RRULE:FREQ=WEEKLY;COUNT=10'])",
        "calendarId": "Optional: The ID of the calendar to create the event on (default: 'primary')"
    },
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title of the event"
            },
            "startTime": {
                "type": "string",
                "description": "The start time of the first occurrence in ISO 8601 format"
            },
            "endTime": {
                "type": "string",
                "description": "The end time of the first occurrence in ISO 8601 format"
            },
            "description": {
                "type": "string",
                "description": "Optional: The description of the event"
            },
            "location": {
                "type": "string",
                "description": "Optional: The physical location for in-person meetings"
            },
            "attendees": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "Optional: List of attendees' email addresses"
            },
            "recurrencePattern": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "Optional: List of RRULE strings (e.g., ['RRULE:FREQ=WEEKLY;COUNT=10'])"
            },
            "calendarId": {
                "type": "string",
                "description": "Optional: The ID of the calendar to create the event on (default: 'primary')"
            }
        },
        "required": ["title", "startTime", "endTime"]
    }
)
def create_recurring_event_handler(current_user, data):
    return common_handler(create_recurring_event, 'title', 'startTime', 'endTime', 'description', 
                         location='location', attendees='attendees', recurrence_pattern='recurrencePattern', 
                         calendar_id='calendarId')(current_user, data)

@vop(
    path="/google/integrations/add_event_reminders",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleAddEventReminders",
    description="Adds reminders to an existing calendar event.",
    params={
        "eventId": "ID of the event to update",
        "reminders": "List of dictionaries with 'method' and 'minutes' keys (e.g., [{'method': 'email', 'minutes': 30}])",
        "calendarId": "Calendar ID (defaults to primary)"
    },
    schema={
        "type": "object",
        "properties": {
            "eventId": {
                "type": "string",
                "description": "ID of the event to update"
            },
            "reminders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["email", "popup"],
                            "description": "Reminder method (email or popup)"
                        },
                        "minutes": {
                            "type": "integer",
                            "description": "Minutes before event to send reminder"
                        }
                    },
                    "required": ["method", "minutes"]
                },
                "description": "List of reminders to add to the event"
            },
            "calendarId": {
                "type": "string",
                "description": "Calendar ID (defaults to primary)",
                "default": "primary"
            }
        },
        "required": ["eventId"]
    }
)
def add_event_reminders_handler(current_user, data):
    return common_handler(
        add_event_reminders, 
        'eventId', 
        reminders=None, 
        calendar_id='primary'
    )(current_user, data)

@vop(
    path="/google/integrations/get_calendar_details",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_read"],
    name="googleGetCalendarDetails",
    description="Retrieves detailed information about a specific calendar.",
    params={
        "calendarId": "The ID of the calendar to retrieve"
    },
    schema={
        "type": "object",
        "properties": {
            "calendarId": {
                "type": "string",
                "description": "The ID of the calendar to retrieve"
            }
        },
        "required": ["calendarId"]
    }
)
def get_calendar_details_handler(current_user, data):
    return common_handler(get_calendar_details, 'calendarId')(current_user, data)

@vop(
    path="/google/integrations/update_calendar",
    type="integration",
    tags=["default", "integration", "google_calendar", "google_calendar_write"],
    name="googleUpdateCalendar",
    description="Updates an existing calendar's details.",
    params={
        "calendarId": "The ID of the calendar to update",
        "name": "New name/summary for the calendar (optional)",
        "description": "New description for the calendar (optional)",
        "timezone": "New timezone for the calendar (optional)"
    },
    schema={
        "type": "object",
        "properties": {
            "calendarId": {
                "type": "string",
                "description": "The ID of the calendar to update"
            },
            "name": {
                "type": ["string"],
                "description": "New name/summary for the calendar (optional)"
            },
            "description": {
                "type": ["string"],
                "description": "New description for the calendar (optional)"
            },
            "timezone": {
                "type": ["string"],
                "description": "New timezone for the calendar (optional)"
            }
        },
        "required": ["calendarId"]
    }
)
def update_calendar_handler(current_user, data):
    return common_handler(
        update_calendar, 
        'calendarId', 
        name=None, 
        description=None, 
        timezone=None
    )(current_user, data)




