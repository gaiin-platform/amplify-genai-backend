import os

import boto3
from pycommon.logger import getLogger

logger = getLogger("o365.admin_config")

_IANA_TO_WINDOWS: dict[str, str] = {
    "America/New_York": "Eastern Standard Time",
    "America/Chicago": "Central Standard Time",
    "America/Denver": "Mountain Standard Time",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Anchorage": "Alaskan Standard Time",
    "Pacific/Honolulu": "Hawaiian Standard Time",
    "Europe/London": "GMT Standard Time",
    "Europe/Paris": "W. Europe Standard Time",
    "Europe/Helsinki": "FLE Standard Time",
    "Europe/Moscow": "Russian Standard Time",
    "Asia/Dubai": "Arabian Standard Time",
    "Asia/Kolkata": "India Standard Time",
    "Asia/Bangkok": "SE Asia Standard Time",
    "Asia/Shanghai": "China Standard Time",
    "Asia/Tokyo": "Tokyo Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time",
    "UTC": "UTC",
}

_WINDOWS_TO_IANA: dict[str, str] = {v: k for k, v in _IANA_TO_WINDOWS.items()}

_cached_default_timezone: str | None = None


def get_default_timezone_windows() -> str:
    """
    Returns the system default timezone in Windows format, as configured in the
    admin panel (stored as an IANA name in DynamoDB).  Falls back to "UTC" if
    no value has been configured or the table is unreachable.

    The result is cached for the lifetime of the Lambda container so that only
    the first invocation incurs a DynamoDB read.
    """
    global _cached_default_timezone
    if _cached_default_timezone is not None:
        return _cached_default_timezone
    try:
        table_name = os.environ.get("AMPLIFY_ADMIN_DYNAMODB_TABLE")
        if table_name:
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(table_name)
            result = table.get_item(Key={"config_id": "defaultTimezone"})
            if "Item" in result:
                tz = result["Item"].get("data")
                if tz and isinstance(tz, str):
                    windows_tz = _IANA_TO_WINDOWS.get(tz, "UTC")
                    _cached_default_timezone = windows_tz
                    return windows_tz
    except Exception as e:
        logger.warning("Could not load defaultTimezone from admin config: %s", str(e))
    _cached_default_timezone = "UTC"
    return _cached_default_timezone


def get_default_timezone_iana() -> str:
    """Returns the system default timezone as an IANA name (e.g. 'America/Chicago')."""
    windows_tz = get_default_timezone_windows()
    return _WINDOWS_TO_IANA.get(windows_tz, "UTC")
