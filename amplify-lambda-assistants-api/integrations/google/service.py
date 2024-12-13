from common.validate import validated
from integrations.google.sheets import get_spreadsheet_rows
from integrations.oauth import MissingCredentialsError


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