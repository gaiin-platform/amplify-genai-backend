from common.validate import validated
from integrations.google.sheets import get_spreadsheet_rows


@validated("get_rows")
def get_sheet_rows(event, context, current_user, name, data):
    sheet_id = data['data']['sheetId']
    cell_range = data['data']['cellRange']
    data = get_spreadsheet_rows(current_user, sheet_id, cell_range)

    return {
        "success": True,
        "data": data
    }