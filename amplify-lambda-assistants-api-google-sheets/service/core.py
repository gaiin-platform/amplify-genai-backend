from common.ops import vop
from common.validate import validated
from integrations.google.sheets import get_spreadsheet_rows, get_sheets_info, get_sheet_names, insert_rows, delete_rows, \
    update_rows, create_spreadsheet
from integrations.oauth import MissingCredentialsError


@vop(
    path="/integrations/google/sheets/get-rows",
    tags=["default"],
    name="getSpreadsheetRows",
    description="Returns the rows from a Google Sheet as JSON.",
    params={
        "sheetId": "The ID of the sheet as a string",
        "cellRange": "The range of cells to read as a string, such as A1:A"
    }
)
@validated("get_rows")
def get_sheet_rows(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']
    cell_range = data['data']['cellRange']

    try:
        data = get_spreadsheet_rows(current_user, sheet_id, cell_range)

        return {
            "success": True,
            "data": data
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unknown server error."
        }


@vop(
    path="/integrations/google/sheets/get-info",
    tags=["default"],
    name="getGoogleSheetsInfo",
    description="Returns information about Google Sheets, including sheet names and sample data.",
    params={
        "sheetId": "The ID of the spreadsheet as a string"
    }
)
@validated("get_google_sheets_info")
def get_google_sheets_info(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']

    try:
        sheets_info = get_sheets_info(current_user, sheet_id)

        return {
            "success": True,
            "data": sheets_info
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unknown server error."
        }


@vop(
    path="/integrations/google/sheets/get-sheet-names",
    tags=["default"],
    name="getSheetNames",
    description="Returns the list of sheet names in a Google Sheets document.",
    params={
        "sheetId": "The ID of the spreadsheet as a string"
    }
)
@validated("get_sheet_names")
def get_sheet_names_handler(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']

    try:
        sheet_names = get_sheet_names(current_user, sheet_id)
        return {
            "success": True,
            "data": sheet_names
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unknown server error."
        }


@vop(
    path="/integrations/google/sheets/insert-rows",
    tags=["default"],
    name="insertRows",
    description="Inserts multiple new rows into a Google Sheet.",
    params={
        "sheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to insert",
        "sheetName": "Optional: The name of the sheet to insert into",
        "insertionPoint": "Optional: The row number to start insertion at"
    }
)
@validated("insert_rows")
def insert_rows_handler(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']
    rows_data = data['data']['rowsData']
    sheet_name = data['data'].get('sheetName')
    insertion_point = data['data'].get('insertionPoint')

    try:
        response = insert_rows(current_user, sheet_id, rows_data, sheet_name, insertion_point)
        return {
            "success": True,
            "data": response
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unknown server error."
        }


@vop(
    path="/integrations/google/sheets/delete-rows",
    tags=["default"],
    name="deleteRows",
    description="Deletes a range of rows from a Google Sheet.",
    params={
        "sheetId": "The ID of the spreadsheet as a string",
        "startRow": "The first row to delete",
        "endRow": "The last row to delete (inclusive)",
        "sheetName": "Optional: The name of the sheet to delete from"
    }
)
@validated("delete_rows")
def delete_rows_handler(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']
    start_row = data['data']['startRow']
    end_row = data['data']['endRow']
    sheet_name = data['data'].get('sheetName')

    try:
        response = delete_rows(current_user, sheet_id, start_row, end_row, sheet_name)
        return {
            "success": True,
            "data": response
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@vop(
    path="/integrations/google/sheets/update-rows",
    tags=["default"],
    name="updateRows",
    description="Updates specified rows in a Google Sheet.",
    params={
        "sheetId": "The ID of the spreadsheet as a string",
        "rowsData": "An array of arrays, each representing a row to update. The first item in each array should be the row number to update. Example to update rows 3 and 8: [[3, 'something'],[8, 'new value 1', 'new value 2']]",
        "sheetName": "Optional: The name of the sheet to update"
    }
)
@validated("update_rows")
def update_rows_handler(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']
    rows_data = data['data']['rowsData']
    sheet_name = data['data'].get('sheetName')

    try:
        response = update_rows(current_user, sheet_id, rows_data, sheet_name)
        return {
            "success": True,
            "data": response
        }
    except MissingCredentialsError as me:
        return {
            "success": False,
            "error": str(me)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def sheets_handler(operation, *required_params, **optional_params):
    def handler(event, context, current_user, name, data):
        try:
            params = {param: data['data'][param] for param in required_params}
            params.update({param: data['data'].get(param) for param in optional_params})
            response = operation(current_user, **params)
            return {
                "success": True,
                "data": response
            }
        except MissingCredentialsError as me:
            return {
                "success": False,
                "error": str(me)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    return handler


@vop(
    path="/integrations/google/sheets/create-spreadsheet",
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

