from googleapiclient.discovery import build
import random
from common.ops import vop
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_sheets"


def get_spreadsheet_rows(current_user, sheet_id, cell_range, sheet_name=None):
    user_credentials = get_user_credentials(current_user, integration_name)

    credentials = Credentials.from_authorized_user_info(user_credentials)
    print("Building Google Sheets service")
    service = build('sheets', 'v4', credentials=credentials)

    print("Reading spreadsheet values")
    sheet = service.spreadsheets()

    if sheet_name:
        range_to_read = f"'{sheet_name}'!{cell_range}"
    else:
        range_to_read = cell_range

    result = sheet.values().get(spreadsheetId=sheet_id, range=range_to_read).execute()

    values = result.get('values', [])
    return values


def get_spreadsheet_columns(current_user, sheet_id, sheet_name=None):
    user_credentials = get_user_credentials(current_user, integration_name)

    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    sheet = service.spreadsheets()
    range_to_read = '1:1' if sheet_name is None else f"'{sheet_name}'!1:1"
    result = sheet.values().get(spreadsheetId=sheet_id, range=range_to_read).execute()

    values = result.get('values', [])
    return values[0] if values else []


def get_sheet_names(current_user, sheet_id):
    user_credentials = get_user_credentials(current_user, integration_name)

    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_names = [sheet['properties']['title'] for sheet in sheets]

    return sheet_names


def get_sheets_info(current_user, sheet_id):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    print("Getting spreadsheet metadata")
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')

    result = {
        "sheet_names": [],
        "sheets_data": {}
    }

    print(f"There are {len(sheets)} sheets in the spreadsheet")

    for sheet in sheets:
        print(f"Getting data for sheet {sheet['properties']['title']}")
        sheet_name = sheet['properties']['title']
        result["sheet_names"].append(sheet_name)

        range_name = f"'{sheet_name}'"
        sheet_data = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=range_name).execute()
        values = sheet_data.get('values', [])

        if not values:
            result["sheets_data"][sheet_name] = {"columns": [], "sample_rows": []}
            continue

        columns = values[0]
        rows = values[1:]

        sample_rows = random.sample(rows, min(5, len(rows)))

        result["sheets_data"][sheet_name] = {
            "columns": columns,
            "sample_rows": sample_rows
        }

    return result


def insert_rows(current_user, sheet_id, rows_data, sheet_name=None, insertion_point=None):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    if not sheet_name:
        sheet_name = service.spreadsheets().get(spreadsheetId=sheet_id).execute()['sheets'][0]['properties']['title']

    if not insertion_point:
        result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"{sheet_name}!A:A").execute()
        insertion_point = len(result.get('values', [])) + 1

    range_name = f"{sheet_name}!A{insertion_point}"
    body = {
        'values': rows_data
    }

    request = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body=body
    )
    response = request.execute()

    return response


def delete_rows(current_user, sheet_id, start_row, end_row, sheet_name=None):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    if not sheet_name:
        sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata['sheets'][0]['properties']['title']

    request = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": get_sheet_id(service, sheet_id, sheet_name),
                        "dimension": "ROWS",
                        "startIndex": start_row - 1,
                        "endIndex": end_row
                    }
                }
            }
        ]
    }

    response = service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=request).execute()
    return response


def get_sheet_id(service, spreadsheet_id, sheet_name):
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    for sheet in sheets:
        if sheet['properties']['title'] == sheet_name:
            return sheet['properties']['sheetId']
    raise ValueError(f"Sheet '{sheet_name}' not found")


def update_rows(current_user, sheet_id, rows_data, sheet_name=None):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    service = build('sheets', 'v4', credentials=credentials)

    if not sheet_name:
        sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata['sheets'][0]['properties']['title']

    requests = []
    for row in rows_data:
        row_number = row[0]
        values = row[1:]
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": get_sheet_id(service, sheet_id, sheet_name),
                    "startRowIndex": row_number - 1,
                    "endRowIndex": row_number,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(values)
                },
                "rows": [{"values": [{"userEnteredValue": {"stringValue": str(value)}} for value in values]}],
                "fields": "userEnteredValue"
            }
        })

    body = {"requests": requests}
    response = service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
    return response


def create_spreadsheet(current_user, title):
    service = get_sheets_service(current_user)
    spreadsheet = {'properties': {'title': title}}
    return service.spreadsheets().create(body=spreadsheet).execute()

def duplicate_sheet(current_user, spreadsheet_id, sheet_id, new_sheet_name):
    service = get_sheets_service(current_user)
    request = {
        'destinationSpreadsheetId': spreadsheet_id,
        'duplicateSheet': {
            'sourceSheetId': sheet_id,
            'insertSheetIndex': 0,
            'newSheetName': new_sheet_name
        }
    }
    return service.spreadsheets().sheets().copyTo(spreadsheetId=spreadsheet_id, sheetId=sheet_id, body=request).execute()

def rename_sheet(current_user, spreadsheet_id, sheet_id, new_name):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'updateSheetProperties': {
                'properties': {'sheetId': sheet_id, 'title': new_name},
                'fields': 'title'
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()

def clear_range(current_user, spreadsheet_id, range_name):
    service = get_sheets_service(current_user)
    return service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=range_name).execute()

def apply_formatting(current_user, spreadsheet_id, sheet_id, start_row, end_row, start_col, end_col, format_json):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'repeatCell': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': start_row - 1,
                    'endRowIndex': end_row,
                    'startColumnIndex': start_col - 1,
                    'endColumnIndex': end_col,
                },
                'cell': {'userEnteredFormat': format_json},
                'fields': 'userEnteredFormat'
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()

def add_chart(current_user, spreadsheet_id, sheet_id, chart_spec):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'addChart': {
                'chart': chart_spec,
                'position': {
                    'sheetId': sheet_id,
                    'overlayPosition': {
                        'anchorCell': {'sheetId': sheet_id, 'rowIndex': 0, 'columnIndex': 0},
                        'offsetXPixels': 0,
                        'offsetYPixels': 0
                    }
                }
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()

def get_cell_formulas(current_user, spreadsheet_id, range_name):
    service = get_sheets_service(current_user)
    return service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_name, valueRenderOption='FORMULA').execute()

def find_replace(current_user, spreadsheet_id, find, replace, sheet_id=None):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'findReplace': {
                'find': find,
                'replacement': replace,
                'allSheets': sheet_id is None,
                'sheetId': sheet_id
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()

def sort_range(current_user, spreadsheet_id, sheet_id, start_row, end_row, start_col, end_col, sort_order):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'sortRange': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': start_row - 1,
                    'endRowIndex': end_row,
                    'startColumnIndex': start_col - 1,
                    'endColumnIndex': end_col,
                },
                'sortSpecs': sort_order
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()

def apply_conditional_formatting(current_user, spreadsheet_id, sheet_id, start_row, end_row, start_col, end_col, condition, format):
    service = get_sheets_service(current_user)
    request = {
        'requests': [{
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{
                        'sheetId': sheet_id,
                        'startRowIndex': start_row - 1,
                        'endRowIndex': end_row,
                        'startColumnIndex': start_col - 1,
                        'endColumnIndex': end_col,
                    }],
                    'booleanRule': {
                        'condition': condition,
                        'format': format
                    }
                },
                'index': 0
            }
        }]
    }
    return service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request).execute()


def get_sheets_service(current_user):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build('sheets', 'v4', credentials=credentials)