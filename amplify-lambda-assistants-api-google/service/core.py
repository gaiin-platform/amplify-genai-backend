from common.ops import vop
from common.validate import validated
from integrations.google.calendar import get_events_between_dates, create_event, check_event_conflicts, \
    get_free_time_slots, get_upcoming_events, get_events_for_date, get_event_details, delete_event, update_event
from integrations.google.docs import find_text_indices, append_text
from integrations.google.sheets import get_spreadsheet_rows, get_sheets_info, get_sheet_names, insert_rows, delete_rows, \
    update_rows, create_spreadsheet, apply_conditional_formatting, sort_range, find_replace, get_cell_formulas, \
    add_chart, apply_formatting, clear_range, rename_sheet, duplicate_sheet, execute_query
from integrations.google.docs import create_new_document, get_document_contents, insert_text, replace_text, create_document_outline, export_document, share_document, find_text_indices
from integrations.oauth import MissingCredentialsError
import re

def camel_to_snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

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
    params={
        "spreadsheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to insert",
        "sheetName": "Optional: The name of the sheet to insert into",
        "insertionPoint": "Optional: The row number to start insertion at"
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
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "find": "The text to find",
        "replace": "The text to replace with",
        "sheetId": "Optional: The ID of the sheet to perform find/replace on"
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
    params={
        "spreadsheetId": "The ID of the spreadsheet",
        "query": "The SQL-like query to execute e.g., a == 1 and b < 34",
        "sheetName": "(Optional) The name of the sheet to query"
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
        "startDate": "The start date in ISO 8601 format",
        "endDate": "The end date in ISO 8601 format"
    }
)
@validated("get_events_between_dates")
def get_events_between_dates_handler(event, context, current_user, name, data):
    return common_handler(get_events_between_dates, 'startDate', 'endDate')(event, context, current_user, name, data)



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
    }
)
@validated("create_event")
def create_event_handler(event, context, current_user, name, data):
    return common_handler(create_event, 'title', 'startTime', 'endTime', 'description')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/update-event",
    tags=["default"],
    name="updateEvent",
    description="Updates an existing event in Google Calendar.",
    params={
        "eventId": "The ID of the event to update",
        "updatedFields": "A dictionary of fields to update and their new values"
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
    }
)
@validated("get_event_details")
def get_event_details_handler(event, context, current_user, name, data):
    return common_handler(get_event_details, 'eventId')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-events-between-dates",
    tags=["default"],
    name="getEventsBetweenDates",
    description="Retrieves events from Google Calendar between two specified dates.",
    params={
        "startDate": "The start date in ISO 8601 format",
        "endDate": "The end date in ISO 8601 format"
    }
)
@validated("get_events_between_dates")
def get_events_between_dates_handler(event, context, current_user, name, data):
    return common_handler(get_events_between_dates, 'startDate', 'endDate')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-events-for-date",
    tags=["default"],
    name="getEventsForDate",
    description="Retrieves events from Google Calendar for a specific date.",
    params={
        "date": "The date in ISO 8601 format (YYYY-MM-DD)"
    }
)
@validated("get_events_for_date")
def get_events_for_date_handler(event, context, current_user, name, data):
    return common_handler(get_events_for_date, 'date')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-upcoming-events",
    tags=["default"],
    name="getUpcomingEvents",
    description="Retrieves upcoming events from Google Calendar until a specified end date.",
    params={
        "endDate": "The end date in ISO 8601 format"
    }
)
@validated("get_upcoming_events")
def get_upcoming_events_handler(event, context, current_user, name, data):
    return common_handler(get_upcoming_events, 'endDate')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/get-free-time-slots",
    tags=["default"],
    name="getFreeTimeSlots",
    description="Finds free time slots in Google Calendar between two dates.",
    params={
        "startDate": "The start date in ISO 8601 format",
        "endDate": "The end date in ISO 8601 format",
        "duration": "The minimum duration of free time slots in minutes"
    }
)
@validated("get_free_time_slots")
def get_free_time_slots_handler(event, context, current_user, name, data):
    return common_handler(get_free_time_slots, 'startDate', 'endDate', 'duration')(event, context, current_user, name, data)

@vop(
    path="/google/integrations/calendar/check-event-conflicts",
    tags=["default"],
    name="checkEventConflicts",
    description="Checks for conflicts with existing events in Google Calendar.",
    params={
        "proposedStartTime": "The proposed start time in ISO 8601 format",
        "proposedEndTime": "The proposed end time in ISO 8601 format"
    }
)
@validated("check_event_conflicts")
def check_event_conflicts_handler(event, context, current_user, name, data):
    return common_handler(check_event_conflicts, 'proposedStartTime', 'proposedEndTime')(event, context, current_user, name, data)