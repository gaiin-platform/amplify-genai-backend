from googleapiclient.discovery import build

from integrations.google.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_sheets"

def get_spreadsheet_rows(current_user, sheet_id, cell_range):
    user_credentials = get_user_credentials(current_user, integration_name)

    credentials = Credentials.from_authorized_user_info(user_credentials)
    print("Building Google Sheets service")
    service = build('sheets', 'v4', credentials=credentials)

    print("Reading spreadsheet values")
    sheet = service.spreadsheets()
    print("Sheet:", sheet)
    result = sheet.values().get(spreadsheetId=sheet_id, range=cell_range).execute()
    print("Result:", result)
    values = result.get('values', [])
    return values