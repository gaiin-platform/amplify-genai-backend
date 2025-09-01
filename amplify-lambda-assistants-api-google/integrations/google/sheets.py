from googleapiclient.discovery import build
import random
import json
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_sheets"


def format_row_as_json(row, index):
    csv_string = ",".join(str(cell).replace(",", "\\,") for cell in row)
    return json.dumps({"index": index, "csv": csv_string})


def get_spreadsheet_rows(
    current_user, spreadsheet_id, cell_range, sheet_name=None, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    range_to_read = f"'{sheet_name}'!{cell_range}" if sheet_name else cell_range
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_to_read)
        .execute()
    )
    return [
        format_row_as_json(row, i + 1) for i, row in enumerate(result.get("values", []))
    ]


def get_spreadsheet_columns(
    current_user, spreadsheet_id, sheet_name=None, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    range_to_read = "1:1" if sheet_name is None else f"'{sheet_name}'!1:1"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_to_read)
        .execute()
    )
    values = result.get("values", [])
    return format_row_as_json(values[0], 1) if values else []


def get_sheets_info(current_user, spreadsheet_id, access_token=None):
    service = get_sheets_service(current_user, access_token)
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get("sheets", "")
    result = {"sheet_names": [], "sheets_data": {}}

    for sheet in sheets:
        sheet_name = sheet["properties"]["title"]
        result["sheet_names"].append(sheet_name)
        range_name = f"'{sheet_name}'"
        sheet_data = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        values = sheet_data.get("values", [])

        if not values:
            result["sheets_data"][sheet_name] = {"columns": [], "sample_rows": []}
            continue

        columns = format_row_as_json(values[0], 1)
        rows = values[1:]
        sample_rows = [
            format_row_as_json(row, i + 2)
            for i, row in enumerate(random.sample(rows, min(5, len(rows))))
        ]
        result["sheets_data"][sheet_name] = {
            "columns": columns,
            "sample_rows": sample_rows,
        }

    return result


def get_cell_formulas(current_user, spreadsheet_id, range_name, access_token=None):
    service = get_sheets_service(current_user, access_token)
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id, range=range_name, valueRenderOption="FORMULA"
        )
        .execute()
    )
    return [
        format_row_as_json(row, i + 1) for i, row in enumerate(result.get("values", []))
    ]


def execute_query(current_user, spreadsheet_id, query, sheet_name, access_token=None):
    service = get_sheets_service(current_user, access_token)

    if sheet_name is None:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_name = spreadsheet["sheets"][0]["properties"]["title"]

    sheet_range = f"{sheet_name}!A1:ZZ"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=sheet_range)
        .execute()
    )
    data = result.get("values", [])

    if not data:
        return []

    headers = data[0]
    records = data[1:]

    conditions = parse_query(query)

    filtered_records = [
        format_row_as_json(record, idx + 2)
        for idx, record in enumerate(records)
        if evaluate_conditions(conditions, dict(zip(headers, record)))
    ]

    return filtered_records


def parse_query(query):
    clauses = query.split(" and ")
    return [parse_clause(clause) for clause in clauses]


def parse_clause(clause):
    or_clauses = clause.split(" or ")
    return [parse_condition(c) for c in or_clauses]


def parse_condition(condition):
    operators = ["==", "!=", ">", "<", ">=", "<=", "in", "not in"]
    for op in operators:
        if op in condition:
            column, value = condition.split(op)
            return column.strip(), op, value.strip()
    raise ValueError("Unsupported query format")


def evaluate_condition(condition, record):
    column, op, value = condition
    if column not in record:
        return False

    record_value = record[column]

    operators = {
        "==": lambda x, y: x == y,
        "!=": lambda x, y: x != y,
        ">": lambda x, y: float(x) > float(y),
        "<": lambda x, y: float(x) < float(y),
        ">=": lambda x, y: float(x) >= float(y),
        "<=": lambda x, y: float(x) <= float(y),
        "in": lambda x, y: x in parse_list(y),
        "not in": lambda x, y: x not in parse_list(y),
    }

    return operators[op](record_value, value)


def parse_list(value):
    return [item.strip() for item in value.strip("[]").split(",")]


def evaluate_conditions(conditions, record):
    return all(
        evaluate_or_conditions(or_conditions, record) for or_conditions in conditions
    )


def evaluate_or_conditions(or_conditions, record):
    return any(evaluate_condition(condition, record) for condition in or_conditions)


def insert_rows(
    current_user,
    spreadsheet_id,
    rows_data,
    sheet_name=None,
    insertion_point=None,
    access_token=None,
):
    service = get_sheets_service(current_user, access_token)
    if not sheet_name:
        sheet_name = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id)
            .execute()["sheets"][0]["properties"]["title"]
        )
    if not insertion_point:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:A")
            .execute()
        )
        insertion_point = len(result.get("values", [])) + 1
    range_name = f"{sheet_name}!A{insertion_point}"
    body = {"values": rows_data}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    return json.dumps(
        {"inserted_rows": len(rows_data), "insertion_point": insertion_point}
    )


def delete_rows(
    current_user, spreadsheet_id, start_row, end_row, sheet_name=None, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    if not sheet_name:
        sheet_metadata = (
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
    sheet_id = get_sheet_id(service, spreadsheet_id, sheet_name)
    request = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": start_row - 1,
                        "endIndex": end_row,
                    }
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {
            "deleted_rows": end_row - start_row + 1,
            "start_row": start_row,
            "end_row": end_row,
        }
    )


def update_rows(
    current_user, spreadsheet_id, rows_data, sheet_name=None, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    if not sheet_name:
        sheet_metadata = (
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
    sheet_id = get_sheet_id(service, spreadsheet_id, sheet_name)
    requests = []
    for row in rows_data:
        row_number = row[0]
        values = row[1:]
        requests.append(
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_number - 1,
                        "endRowIndex": row_number,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(values),
                    },
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": str(value)}}
                                for value in values
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        )
    body = {"requests": requests}
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return json.dumps({"updated_rows": len(rows_data)})


def create_spreadsheet(current_user, title, access_token=None):
    service = get_sheets_service(current_user, access_token)
    spreadsheet = {"properties": {"title": title}}
    result = service.spreadsheets().create(body=spreadsheet).execute()
    return json.dumps(
        {
            "spreadsheet_id": result["spreadsheetId"],
            "title": result["properties"]["title"],
        }
    )


def duplicate_sheet(
    current_user, spreadsheet_id, sheet_id, new_sheet_name, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    request = {
        "destinationSpreadsheetId": spreadsheet_id,
        "duplicateSheet": {
            "sourceSheetId": sheet_id,
            "insertSheetIndex": 0,
            "newSheetName": new_sheet_name,
        },
    }
    result = (
        service.spreadsheets()
        .sheets()
        .copyTo(spreadsheetId=spreadsheet_id, sheetId=sheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {"new_sheet_id": result["sheetId"], "new_sheet_name": result["title"]}
    )


def rename_sheet(current_user, spreadsheet_id, sheet_id, new_name, access_token=None):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "title": new_name},
                    "fields": "title",
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps({"sheet_id": sheet_id, "new_name": new_name})


def clear_range(current_user, spreadsheet_id, range_name, access_token=None):
    service = get_sheets_service(current_user, access_token)
    result = (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return json.dumps({"cleared_range": range_name})


def apply_formatting(
    current_user,
    spreadsheet_id,
    sheet_id,
    start_row,
    end_row,
    start_col,
    end_col,
    format_json,
    access_token=None,
):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {"userEnteredFormat": format_json},
                    "fields": "userEnteredFormat",
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {"formatted_range": f"{start_row}:{end_row},{start_col}:{end_col}"}
    )


def add_chart(current_user, spreadsheet_id, sheet_id, chart_spec, access_token=None):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "addChart": {
                    "chart": chart_spec,
                    "position": {
                        "sheetId": sheet_id,
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": sheet_id,
                                "rowIndex": 0,
                                "columnIndex": 0,
                            },
                            "offsetXPixels": 0,
                            "offsetYPixels": 0,
                        },
                    },
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {"chart_id": result["replies"][0]["addChart"]["chart"]["chartId"]}
    )


def find_replace(
    current_user, spreadsheet_id, find, replace, sheet_id=None, access_token=None
):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "findReplace": {
                    "find": find,
                    "replacement": replace,
                    "allSheets": sheet_id is None,
                    "sheetId": sheet_id,
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {
            "occurrences_changed": result["replies"][0]["findReplace"][
                "occurrencesChanged"
            ]
        }
    )


def sort_range(
    current_user,
    spreadsheet_id,
    sheet_id,
    start_row,
    end_row,
    start_col,
    end_col,
    sort_order,
    access_token=None,
):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "sortRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "sortSpecs": sort_order,
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps({"sorted_range": f"{start_row}:{end_row},{start_col}:{end_col}"})


def apply_conditional_formatting(
    current_user,
    spreadsheet_id,
    sheet_id,
    start_row,
    end_row,
    start_col,
    end_col,
    condition,
    format,
    access_token=None,
):
    service = get_sheets_service(current_user, access_token)
    request = {
        "requests": [
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row - 1,
                                "endRowIndex": end_row,
                                "startColumnIndex": start_col - 1,
                                "endColumnIndex": end_col,
                            }
                        ],
                        "booleanRule": {"condition": condition, "format": format},
                    },
                    "index": 0,
                }
            }
        ]
    }
    result = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request)
        .execute()
    )
    return json.dumps(
        {"formatted_range": f"{start_row}:{end_row},{start_col}:{end_col}"}
    )


def get_sheet_id(service, spreadsheet_id, sheet_name):
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get("sheets", "")
    for sheet in sheets:
        if sheet["properties"]["title"] == sheet_name:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"Sheet '{sheet_name}' not found")


def get_sheets_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("sheets", "v4", credentials=credentials)


def get_sheet_names(current_user, spreadsheet_id, access_token=None):
    service = get_sheets_service(current_user, access_token)
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get("sheets", "")
    return [sheet["properties"]["title"] for sheet in sheets]
