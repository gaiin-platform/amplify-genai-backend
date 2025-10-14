import json
import requests
from typing import Dict, List, Union, Optional, Any
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_excel"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class ExcelError(Exception):
    """Base exception for Excel operations"""

    pass


class WorksheetNotFoundError(ExcelError):
    """Raised when worksheet cannot be found"""

    pass


class TableNotFoundError(ExcelError):
    """Raised when table cannot be found"""

    pass


class RangeError(ExcelError):
    """Raised when range operations fail"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "")
        if "worksheet" in error_message.lower():
            raise WorksheetNotFoundError("Worksheet not found")
        elif "table" in error_message.lower():
            raise TableNotFoundError("Table not found")
        raise ExcelError("Resource not found")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise ExcelError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_worksheets(current_user: str, item_id: str, access_token: str) -> List[Dict]:
    """
    Lists worksheets in a workbook stored in OneDrive.

    Args:
        current_user: User identifier
        item_id: OneDrive item ID of the workbook

    Returns:
        List of worksheet details

    Raises:
        ExcelError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return [format_worksheet(sheet) for sheet in response.json().get("value", [])]

    except requests.RequestException as e:
        raise ExcelError(f"Network error while listing worksheets: {str(e)}")


def list_tables(
    current_user: str,
    item_id: str,
    worksheet_name: Optional[str] = None,
    access_token: str = None,
) -> List[Dict]:
    """
    Lists tables in a workbook or specific worksheet.

    Args:
        current_user: User identifier
        item_id: OneDrive item ID of the workbook
        worksheet_name: Optional worksheet name to filter tables

    Returns:
        List of table details

    Raises:
        WorksheetNotFoundError: If specified worksheet doesn't exist
        ExcelError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        base_url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook"

        if worksheet_name:
            url = f"{base_url}/worksheets/{worksheet_name}/tables"
        else:
            url = f"{base_url}/tables"

        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return [format_table(table) for table in response.json().get("value", [])]

    except requests.RequestException as e:
        raise ExcelError(f"Network error while listing tables: {str(e)}")


def add_row_to_table(
    current_user: str,
    item_id: str,
    table_name: str,
    row_values: List[Any],
    access_token: str,
) -> Dict:
    """
    Adds a row to a table in the workbook.

    Args:
        current_user: User identifier
        item_id: OneDrive item ID of the workbook
        table_name: Name of the table
        row_values: List of cell values for the new row

    Returns:
        Dict containing added row details

    Raises:
        TableNotFoundError: If table doesn't exist
        ExcelError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/tables/{table_name}/rows/add"

        # Validate row_values
        if not isinstance(row_values, list):
            raise ExcelError("row_values must be a list")

        body = {"values": [row_values]}
        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_table_row(response.json())

    except requests.RequestException as e:
        raise ExcelError(f"Network error while adding row: {str(e)}")


def read_range(
    current_user: str,
    item_id: str,
    worksheet_name: str,
    address: str,
    access_token: str,
) -> List[List[Any]]:
    """
    Reads a range in the given worksheet.

    Args:
        current_user: User identifier
        item_id: OneDrive item ID of the workbook
        worksheet_name: Name of the worksheet
        address: Range address (e.g., 'A1:C10')

    Returns:
        2D list containing cell values

    Raises:
        WorksheetNotFoundError: If worksheet doesn't exist
        RangeError: If range address is invalid
        ExcelError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Validate range address format
        if not _is_valid_range_address(address):
            raise RangeError(f"Invalid range address format: {address}")

        url = (
            f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}"
            f"/range(address='{address}')"
        )

        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return response.json().get("values", [])

    except requests.RequestException as e:
        raise ExcelError(f"Network error while reading range: {str(e)}")


def update_range(
    current_user: str,
    item_id: str,
    worksheet_name: str,
    address: str,
    values: List[List[Any]],
    access_token: str,
) -> Dict:
    """
    Updates a range in the given worksheet.

    Args:
        current_user: User identifier
        item_id: OneDrive item ID of the workbook
        worksheet_name: Name of the worksheet
        address: Range address (e.g., 'A1:C10')
        values: 2D list of values to update

    Returns:
        Dict containing update status

    Raises:
        WorksheetNotFoundError: If worksheet doesn't exist
        RangeError: If range address is invalid
        ExcelError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        if not _is_valid_range_address(address):
            raise RangeError(f"Invalid range address format: {address}")

        url = (
            f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}"
            f"/range(address='{address}')"
        )

        body = {"values": values}
        response = session.patch(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return {"status": "updated", "address": address}

    except requests.RequestException as e:
        raise ExcelError(f"Network error while updating range: {str(e)}")


def _is_valid_range_address(address: str) -> bool:
    """Validates Excel range address format"""
    import re

    pattern = r"^[A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*$"
    return bool(re.match(pattern, address))


def format_worksheet(worksheet: Dict) -> Dict:
    """Format worksheet data consistently"""
    return {
        "id": worksheet["id"],
        "name": worksheet.get("name", ""),
        "position": worksheet.get("position", 0),
        "visibility": worksheet.get("visibility", "Visible"),
        "tabColor": worksheet.get("tabColor", ""),
    }


def format_table(table: Dict) -> Dict:
    """Format table data consistently"""
    return {
        "id": table["id"],
        "name": table.get("name", ""),
        "showHeaders": table.get("showHeaders", True),
        "showTotals": table.get("showTotals", False),
        "style": table.get("style", ""),
        "highlightFirstColumn": table.get("highlightFirstColumn", False),
        "highlightLastColumn": table.get("highlightLastColumn", False),
        "rowCount": table.get("rows", {}).get("count", 0),
        "columnCount": table.get("columns", {}).get("count", 0),
    }


def format_table_row(row: Dict) -> Dict:
    """Format table row data consistently"""
    return {
        "index": row.get("index", 0),
        "values": row.get("values", [[]])[0],
    }


def get_worksheet(
    current_user: str, item_id: str, worksheet_name: str, access_token: str
) -> Dict:
    """Retrieves details of a specific worksheet."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return format_worksheet(response.json())
    except requests.RequestException as e:
        raise ExcelError(f"Network error while retrieving worksheet: {str(e)}")


def create_worksheet(
    current_user: str, item_id: str, name: str, access_token: str
) -> Dict:
    """Creates a new worksheet in the workbook."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/add"
        body = {"name": name}
        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)
        return format_worksheet(response.json())
    except requests.RequestException as e:
        raise ExcelError(f"Network error while creating worksheet: {str(e)}")


def delete_worksheet(
    current_user: str, item_id: str, worksheet_name: str, access_token: str
) -> Dict:
    """Deletes a worksheet from the workbook."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}"
        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "worksheet_name": worksheet_name}
        handle_graph_error(response)
    except requests.RequestException as e:
        raise ExcelError(f"Network error while deleting worksheet: {str(e)}")


def create_table(
    current_user: str,
    item_id: str,
    worksheet_name: str,
    address: str,
    has_headers: bool = True,
    access_token: str = None,
) -> Dict:
    """Creates a new table in a worksheet.

    Args:
        item_id: OneDrive item ID of the workbook.
        worksheet_name: Worksheet name.
        address: Range address for the table (e.g., 'A1:D4').
        has_headers: Specifies if the table has headers.

    Returns:
        Created table details.
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}/tables/add"
        body = {"address": address, "hasHeaders": has_headers}
        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise ExcelError(f"Network error while creating table: {str(e)}")


def delete_table(
    current_user: str, item_id: str, table_id: str, access_token: str
) -> Dict:
    """Deletes an existing table from the workbook."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/tables/{table_id}"
        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "table_id": table_id}
        handle_graph_error(response)
    except requests.RequestException as e:
        raise ExcelError(f"Network error while deleting table: {str(e)}")


def get_table_range(
    current_user: str, item_id: str, table_id: str, access_token: str
) -> Dict:
    """Retrieves the range of a specified table."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/tables/{table_id}/range"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise ExcelError(f"Network error while retrieving table range: {str(e)}")


def list_charts(
    current_user: str, item_id: str, worksheet_name: str, access_token: str
) -> List[Dict]:
    """Lists all charts in a specified worksheet."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}/charts"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json().get("value", [])
    except requests.RequestException as e:
        raise ExcelError(f"Network error while listing charts: {str(e)}")


def get_chart(
    current_user: str, item_id: str, worksheet_name: str, chart_name: str, access_token: str
) -> Dict:
    """Retrieves details of a specified chart."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}/charts/{chart_name}"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise ExcelError(f"Network error while retrieving chart: {str(e)}")


def create_chart(
    current_user: str,
    item_id: str,
    worksheet_name: str,
    chart_type: str,
    source_range: str,
    series_by: str,
    title: str = "",
    access_token: str = None,
) -> Dict:
    """Creates a new chart in a worksheet.

    Args:
        chart_type: Type of chart (e.g., 'ColumnClustered', 'Line', etc.)
        source_range: Range address for the data (e.g., 'A1:D5')
        seriesBy: How series are grouped (e.g., 'Auto', 'Columns', or 'Rows')
        title: Optional chart title.

    Returns:
        Created chart details.
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}/charts/add"
        body = {
            "type": chart_type,
            "sourceData": source_range,
            "seriesBy": series_by,
        }
        if title:
            body["title"] = {"text": title}
        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise ExcelError(f"Network error while creating chart: {str(e)}")


def delete_chart(
    current_user: str, item_id: str, worksheet_name: str, chart_name: str, access_token: str
) -> Dict:
    """Deletes a specified chart from a worksheet."""
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/drive/items/{item_id}/workbook/worksheets/{worksheet_name}/charts/{chart_name}"
        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "chart_name": chart_name}
        handle_graph_error(response)
    except requests.RequestException as e:
        raise ExcelError(f"Network error while deleting chart: {str(e)}")
        raise ExcelError(f"Network error while deleting chart: {str(e)}")
        raise ExcelError(f"Network error while deleting chart: {str(e)}")
