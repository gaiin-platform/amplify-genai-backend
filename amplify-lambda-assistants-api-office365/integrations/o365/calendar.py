import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from integrations.oauth import get_ms_graph_session
from typing import Dict, List, Optional
import base64

integration_name = "microsoft_calendar"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class CalendarError(Exception):
    """Base exception for calendar operations"""

    pass


class EventNotFoundError(CalendarError):
    """Raised when an event cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        raise EventNotFoundError("Event not found")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise CalendarError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def create_event(
    current_user: str,
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    access_token: str = None,
    location: str = None,
    attendees: List[Dict] = None,
    calendar_id: str = None,
    is_online_meeting: bool = False,
    reminder_minutes_before_start: int = None,
    send_invitations: str = "auto",
    time_zone: str = "Central Standard Time",
) -> Dict:
    """
    Create a calendar event with specified details.

    Args:
        current_user: The user's identifier
        title: Event title/subject
        start_time: Start time in ISO format
        end_time: End time in ISO format
        description: Event description/content
        access_token: Optional access token
        location: Physical location for the meeting
        attendees: List of attendee objects with email addresses
        calendar_id: ID of calendar to create event in (default: primary calendar)
        is_online_meeting: Whether to create as an online Teams meeting
        reminder_minutes_before_start: Set reminder in minutes before event start
        send_invitations: Whether to send invitations: "auto", "send", "none"
        time_zone: Time zone for the event times (Windows format)

    Returns:
        Created event details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Validate datetime formats
        for dt in [start_time, end_time]:
            try:
                datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                raise CalendarError(f"Invalid datetime format: {dt}")

        # Build the event body
        event_body = {
            "subject": title,
            "body": {"contentType": "text", "content": description},
            "start": {"dateTime": start_time, "timeZone": time_zone},
            "end": {"dateTime": end_time, "timeZone": time_zone},
        }

        # Add location if provided
        if location:
            event_body["location"] = {"displayName": location}

        # Add attendees if provided
        if attendees:
            event_body["attendees"] = [
                {
                    "emailAddress": {"address": attendee.get("email")},
                    "type": attendee.get("type", "required"),
                }
                for attendee in attendees
            ]

        # Enable online meeting if requested
        if is_online_meeting:
            event_body["isOnlineMeeting"] = True
            event_body["onlineMeetingProvider"] = "teamsForBusiness"

        # Set reminder if provided
        if reminder_minutes_before_start is not None:
            event_body["reminderMinutesBeforeStart"] = reminder_minutes_before_start

        # Configure send invitation behavior
        if send_invitations:
            if send_invitations not in ["auto", "send", "none"]:
                send_invitations = "auto"

        # Determine the endpoint based on calendar_id
        if calendar_id:
            url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/events"
        else:
            url = f"{GRAPH_ENDPOINT}/me/events"

        # Add header for invitation sending preference
        headers = {"Prefer": f"outlook.sending-invitations={send_invitations}"}

        response = session.post(url, json=event_body, headers=headers)

        if not response.ok:
            handle_graph_error(response)

        created_event = response.json()
        return format_event(created_event)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while creating event: {str(e)}")


def update_event(current_user, event_id, updated_fields, access_token):

    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"

        # Validate datetime formats if present
        for time_field in ["start", "end"]:
            if time_field in updated_fields:
                try:
                    dt = updated_fields[time_field]["dateTime"]
                    datetime.fromisoformat(dt.replace("Z", "+00:00"))
                except (ValueError, KeyError):
                    raise CalendarError(f"Invalid {time_field} datetime format")

        response = session.patch(url, json=updated_fields)

        if not response.ok:
            handle_graph_error(response)

        updated_event = response.json()
        return format_event(updated_event)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while updating event: {str(e)}")


def delete_event(current_user, event_id, access_token):
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": event_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting event: {str(e)}")


def get_event_details(current_user, event_id, access_token, user_timezone: str = "UTC"):
    """
    Get details for a specific calendar event.

    Args:
        current_user: The user's identifier
        event_id: ID of the event to retrieve
        access_token: Optional access token
        user_timezone: User's preferred timezone (Windows format)
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"

        # Add timezone preference header to get event in user's timezone
        headers = {}
        if user_timezone and user_timezone != "UTC":
            headers["Prefer"] = f'outlook.timezone="{user_timezone}"'

        response = session.get(url, headers=headers)

        if not response.ok:
            handle_graph_error(response)

        event = response.json()
        return format_event(event)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching event: {str(e)}")


def get_events_between_dates(
    current_user,
    start_dt,
    end_dt,
    page_size: int = 50,
    access_token: str = None,
    user_timezone: str = "UTC",
):
    """
    Retrieves events between two date/times, e.g. '2025-01-30T00:00:00Z' to '2025-01-31T23:59:59Z'.
    Uses /calendarView endpoint for easy range queries.

    Args:
        current_user: The user's identifier
        start_dt: Start datetime in ISO format
        end_dt: End datetime in ISO format
        page_size: Maximum number of events to retrieve per page
        access_token: Optional access token
        user_timezone: User's preferred timezone (Windows format)
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = (
            f"{GRAPH_ENDPOINT}/me/calendarView?"
            f"startDateTime={start_dt}&endDateTime={end_dt}"
            f"&$orderby=start/dateTime&$top={page_size}"
        )

        # Add timezone preference header to get events in user's timezone
        headers = {}
        if user_timezone and user_timezone != "UTC":
            headers["Prefer"] = f'outlook.timezone="{user_timezone}"'

        all_events = []
        while url:
            response = session.get(url, headers=headers)
            if not response.ok:
                handle_graph_error(response)

            data = response.json()
            events = data.get("value", [])
            all_events.extend([format_event(evt) for evt in events])

            # Handle pagination
            url = data.get("@odata.nextLink")

        return all_events

    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching events: {str(e)}")


def format_event(event: Dict) -> Dict:
    """
    Format event data consistently with proper timezone handling.
    Converts UTC times to Central Time before returning.

    Args:
        event: Raw event data from Graph API

    Returns:
        Dict containing formatted event details with timezone information
    """
    # Get UTC times from the event
    start_utc_str = event.get("start", {}).get("dateTime", "")
    end_utc_str = event.get("end", {}).get("dateTime", "")

    # Convert UTC to Central Time
    start_ct_str = start_utc_str
    end_ct_str = end_utc_str
    start_timezone = "Central Standard Time"
    end_timezone = "Central Standard Time"

    if start_utc_str:
        try:
            # Parse UTC datetime (format: "2025-11-14T15:00:00.0000000" or "2025-11-14T15:00:00Z")
            # Handle format with microseconds
            if "." in start_utc_str:
                # Remove extra digits after decimal to get standard format
                parts = start_utc_str.split(".")
                date_part = parts[0]
                # Keep only first 6 digits of microseconds for parsing
                micro_part = (
                    parts[1][:6] if len(parts[1]) >= 6 else parts[1].ljust(6, "0")
                )
                start_utc_clean = f"{date_part}.{micro_part}"
            else:
                start_utc_clean = start_utc_str

            # Parse and ensure UTC timezone
            start_dt_utc = datetime.fromisoformat(
                start_utc_clean.replace("Z", "+00:00")
            )
            if start_dt_utc.tzinfo is None:
                start_dt_utc = start_dt_utc.replace(tzinfo=ZoneInfo("UTC"))

            # Convert to Central Time
            start_dt_ct = start_dt_utc.astimezone(ZoneInfo("America/Chicago"))
            # Format to match API format: "2025-11-14T15:00:00.0000000" (7 digits after decimal)
            microseconds = start_dt_ct.strftime("%f")
            start_ct_str = (
                start_dt_ct.strftime("%Y-%m-%dT%H:%M:%S") + f".{microseconds}0"
            )

            # Determine if it's CST or CDT
            if start_dt_ct.dst() != timedelta(0):
                start_timezone = "Central Daylight Time"
            else:
                start_timezone = "Central Standard Time"
        except (ValueError, AttributeError) as e:
            # If parsing fails, keep original UTC time
            start_ct_str = start_utc_str
            start_timezone = event.get("start", {}).get("timeZone", "UTC")

    if end_utc_str:
        try:
            # Parse UTC datetime (format: "2025-11-14T15:00:00.0000000" or "2025-11-14T15:00:00Z")
            # Handle format with microseconds
            if "." in end_utc_str:
                # Remove extra digits after decimal to get standard format
                parts = end_utc_str.split(".")
                date_part = parts[0]
                # Keep only first 6 digits of microseconds for parsing
                micro_part = (
                    parts[1][:6] if len(parts[1]) >= 6 else parts[1].ljust(6, "0")
                )
                end_utc_clean = f"{date_part}.{micro_part}"
            else:
                end_utc_clean = end_utc_str

            # Parse and ensure UTC timezone
            end_dt_utc = datetime.fromisoformat(end_utc_clean.replace("Z", "+00:00"))
            if end_dt_utc.tzinfo is None:
                end_dt_utc = end_dt_utc.replace(tzinfo=ZoneInfo("UTC"))

            # Convert to Central Time
            end_dt_ct = end_dt_utc.astimezone(ZoneInfo("America/Chicago"))
            # Format to match API format: "2025-11-14T15:00:00.0000000" (7 digits after decimal)
            microseconds = end_dt_ct.strftime("%f")
            end_ct_str = end_dt_ct.strftime("%Y-%m-%dT%H:%M:%S") + f".{microseconds}0"

            # Determine if it's CST or CDT
            if end_dt_ct.dst() != timedelta(0):
                end_timezone = "Central Daylight Time"
            else:
                end_timezone = "Central Standard Time"
        except (ValueError, AttributeError) as e:
            # If parsing fails, keep original UTC time
            end_ct_str = end_utc_str
            end_timezone = event.get("end", {}).get("timeZone", "UTC")

    return {
        "id": event.get("id", ""),
        "subject": event.get("subject", ""),
        "start": start_ct_str,
        "startTimeZone": start_timezone,
        "end": end_ct_str,
        "endTimeZone": end_timezone,
        "originalStartTimeZone": event.get("originalStartTimeZone", ""),
        "originalEndTimeZone": event.get("originalEndTimeZone", ""),
        "bodyPreview": event.get("bodyPreview", ""),
        "location": event.get("location", {}).get("displayName", ""),
        "organizer": event.get("organizer", {}).get("emailAddress", {}).get("name", ""),
        "status": event.get("showAs", ""),
        "isOnlineMeeting": event.get("isOnlineMeeting", False),
        "onlineMeetingUrl": (
            event.get("onlineMeeting").get("joinUrl", "")
            if event.get("onlineMeeting")
            else ""
        ),
    }


def list_calendars(
    current_user: str, access_token: str = None, include_shared: bool = False
) -> List[Dict]:
    """
    Retrieve all calendars available in the user's mailbox, including those in different groups.

    Args:
        current_user: The user's identifier
        access_token: Optional access token
        include_shared: Whether to include calendars shared with the user (default: True)

    Returns:
        List of calendar objects with their properties
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        result_calendars = []

        # 1. Get all direct calendars first (primary approach)
        url = f"{GRAPH_ENDPOINT}/me/calendars"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)

        calendars = response.json().get("value", [])
        print(f"Calendars: {calendars}")

        # Process direct calendars
        for cal in calendars:
            # Skip United States holidays calendar
            if cal.get("name") == "United States holidays":
                continue

            # Determine if this is a shared calendar
            owner_email = (
                cal.get("owner", {}).get("address", "").lower()
                if cal.get("owner", {}).get("address")
                else ""
            )
            current_user_email = current_user.lower()
            is_shared = owner_email and owner_email != current_user_email

            # Skip shared calendars if not requested
            if is_shared and not include_shared:
                continue

            result_calendars.append(
                {
                    "id": cal["id"],
                    "name": cal.get("name", ""),
                    "color": cal.get("color", ""),
                    "isDefaultCalendar": cal.get("isDefaultCalendar", False),
                    "canShare": cal.get("canShare", False),
                    "canViewPrivateItems": cal.get("canViewPrivateItems", False),
                    "canEdit": cal.get("canEdit", False),
                    "owner": cal.get("owner", {}).get("name", ""),
                    "ownerEmail": owner_email,
                    "isSharedWithMe": is_shared,
                    "source": "direct",
                }
            )

        # 2. Get calendars from all calendar groups (includes "People's Calendars")
        groups_url = f"{GRAPH_ENDPOINT}/me/calendarGroups"
        groups_response = session.get(groups_url)
        print(f"Groups Response: {groups_response.json()}")
        if groups_response.ok:
            calendar_groups = groups_response.json().get("value", [])

            for group in calendar_groups:
                group_id = group.get("id")
                group_name = group.get("name", "")
                if group_name == "My Calendars":
                    continue
                # Get calendars for this group
                group_calendars_url = (
                    f"{GRAPH_ENDPOINT}/me/calendarGroups/{group_id}/calendars"
                )
                group_cal_response = session.get(group_calendars_url)
                print(f"Group Calendars Response: {group_cal_response.json()}")
                if group_cal_response.ok:
                    group_calendars = group_cal_response.json().get("value", [])

                    for cal in group_calendars:
                        # Skip if already in our list (by ID) or if it's US holidays
                        if cal.get("name") == "United States holidays":
                            continue

                        if any(
                            existing["id"] == cal["id"] for existing in result_calendars
                        ):
                            continue

                        # Determine if this is a shared calendar
                        owner_email = (
                            cal.get("owner", {}).get("address", "").lower()
                            if cal.get("owner", {}).get("address")
                            else ""
                        )
                        current_user_email = current_user.lower()
                        is_shared = owner_email and owner_email != current_user_email

                        # Skip shared calendars if not requested
                        if is_shared and not include_shared:
                            continue

                        result_calendars.append(
                            {
                                "id": cal["id"],
                                "name": cal.get("name", ""),
                                "color": cal.get("color", ""),
                                "isDefaultCalendar": cal.get(
                                    "isDefaultCalendar", False
                                ),
                                "canShare": cal.get("canShare", False),
                                "canViewPrivateItems": cal.get(
                                    "canViewPrivateItems", False
                                ),
                                "canEdit": cal.get("canEdit", False),
                                "owner": cal.get("owner", {}).get("name", ""),
                                "ownerEmail": owner_email,
                                "isSharedWithMe": is_shared,
                                "calendarGroup": group_name,
                                "source": "group",
                            }
                        )

        return result_calendars

    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching calendars: {str(e)}")


def create_calendar(
    current_user: str, name: str, color: Optional[str] = None, access_token: str = None
) -> Dict:
    """
    Create a new calendar for the user.

    Args:
        current_user: The user's identifier
        name: Name of the new calendar
        color: Optional color for the calendar (e.g., 'lightBlue', 'lightGreen', etc.)

    Returns:
        Created calendar object
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars"

        calendar_body = {"name": name}
        if color:
            calendar_body["color"] = color

        response = session.post(url, json=calendar_body)
        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise CalendarError(f"Network error while creating calendar: {str(e)}")


def delete_calendar(current_user: str, calendar_id: str, access_token: str) -> Dict:
    """
    Delete a calendar from the user's mailbox.

    Args:
        current_user: The user's identifier
        calendar_id: ID of the calendar to delete

    Returns:
        Status dictionary
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}"

        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "deleted", "id": calendar_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting calendar: {str(e)}")


def respond_to_event(
    current_user: str,
    event_id: str,
    response_type: str,
    comment: Optional[str] = None,
    send_response: bool = True,
    access_token: str = None,
) -> Dict:
    """
    Respond to an event invitation.

    Args:
        current_user: The user's identifier
        event_id: ID of the event to respond to
        response_type: One of 'accept', 'decline', or 'tentativelyAccept'
        comment: Optional comment to include with the response
        send_response: Whether to send a response email

    Returns:
        Response status
    """
    valid_responses = {"accept", "decline", "tentativelyAccept"}
    if response_type not in valid_responses:
        raise ValueError(f"Invalid response_type. Must be one of: {valid_responses}")

    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}/{response_type}"

        body = {"sendResponse": send_response}
        if comment:
            body["comment"] = comment

        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)

        return {"status": "success", "response_type": response_type}

    except requests.RequestException as e:
        raise CalendarError(f"Network error while responding to event: {str(e)}")


def find_meeting_times(
    current_user: str,
    attendees: List[Dict] = None,
    duration_minutes: int = 30,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    time_zone: str = "Central Standard Time",  # Use Windows time zone format
    required_attendees: Optional[List[Dict]] = None,
    optional_attendees: Optional[List[Dict]] = None,
    working_hours_start: Optional[str] = "09:00",
    working_hours_end: Optional[str] = "17:00",
    include_weekends: bool = False,
    availability_view_interval: int = 30,
    access_token: str = None,
) -> Dict:
    """
    Find available meeting times for a group of attendees.

    Args:
        current_user: The user's identifier
        attendees: List of attendee dictionaries with 'email' key (all attendees if not separating required/optional)
        duration_minutes: Length of the meeting in minutes (default: 30)
        start_time: Optional start time boundary (ISO format, default: now)
        end_time: Optional end time boundary (ISO format, default: 7 days from now)
        time_zone: Time zone for the meeting times (Windows format, e.g., 'Central Standard Time')
                  Common values: 'Pacific Standard Time', 'Eastern Standard Time', 'UTC'
        required_attendees: List of required attendee dictionaries with 'email' key
        optional_attendees: List of optional attendee dictionaries with 'email' key
        working_hours_start: Start of working hours in 24-hour format (HH:MM)
        working_hours_end: End of working hours in 24-hour format (HH:MM)
        include_weekends: Whether to include weekends in suggestions
        availability_view_interval: Interval in minutes for checking availability
        access_token: Optional access token

    Returns:
        Dictionary containing suggested meeting times
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{current_user}/findMeetingTimes"

        # Default times if not provided
        if not start_time:
            start_time = datetime.now().isoformat()
        if not end_time:
            end_time = (datetime.now() + timedelta(days=7)).isoformat()

        # Prepare attendees based on required/optional distinction
        formatted_attendees = []

        if required_attendees or optional_attendees:
            if required_attendees:
                for attendee in required_attendees:
                    formatted_attendees.append(
                        {
                            "emailAddress": {"address": attendee["email"]},
                            "type": "required",
                        }
                    )
            if optional_attendees:
                for attendee in optional_attendees:
                    formatted_attendees.append(
                        {
                            "emailAddress": {"address": attendee["email"]},
                            "type": "optional",
                        }
                    )
        else:
            # Use the general attendees list if no required/optional distinction
            # Check if attendees is None or empty, default to using current_user's email
            if not attendees:
                formatted_attendees = [{"emailAddress": {"address": current_user}}]
            else:
                formatted_attendees = [
                    {"emailAddress": {"address": a["email"]}} for a in attendees
                ]

        # Build request body
        meeting_body = {
            "attendees": formatted_attendees,
            "timeConstraint": {
                "timeslots": [
                    {
                        "start": {"dateTime": start_time, "timeZone": time_zone},
                        "end": {"dateTime": end_time, "timeZone": time_zone},
                    }
                ]
            },
            "meetingDuration": f"PT{duration_minutes}M",
            "returnSuggestionReasons": True,
            "availabilityViewInterval": availability_view_interval,
        }

        # Add working hours constraints
        if working_hours_start and working_hours_end:
            meeting_body["locationConstraint"] = {
                "isRequired": False,
                "suggestLocation": False,
            }
            meeting_body["timeConstraint"]["activityDomain"] = "work"

            # Convert working hours to proper format
            work_hours = []
            days = ["monday", "tuesday", "wednesday", "thursday", "friday"]
            if include_weekends:
                days.extend(["saturday", "sunday"])

            for day in days:
                work_hours.append(
                    {
                        "day": day,
                        "timeSlots": [
                            {"start": working_hours_start, "end": working_hours_end}
                        ],
                    }
                )

            meeting_body["isOrganizerOptional"] = False
            meeting_body["meetingTimeSlot"] = {
                "start": {"dateTime": start_time, "timeZone": time_zone},
                "end": {"dateTime": end_time, "timeZone": time_zone},
            }

            meeting_body["workingHours"] = {
                "daysOfWeek": days,
                "startTime": working_hours_start,
                "endTime": working_hours_end,
                "timeZone": {"name": time_zone},
            }

        response = session.post(url, json=meeting_body)
        if not response.ok:
            handle_graph_error(response)

        result = response.json()

        # Format the result for easier consumption
        formatted_result = {"meetingTimeSuggestions": []}

        for suggestion in result.get("meetingTimeSuggestions", []):
            formatted_suggestion = {
                "confidence": suggestion.get("confidence"),
                "organizerAvailability": suggestion.get("organizerAvailability"),
                "attendeeAvailability": suggestion.get("attendeeAvailability", []),
                "locations": suggestion.get("locations", []),
                "suggestionReason": suggestion.get("suggestionReason", ""),
                "meetingTimeSlot": {
                    "start": suggestion.get("meetingTimeSlot", {})
                    .get("start", {})
                    .get("dateTime", ""),
                    "end": suggestion.get("meetingTimeSlot", {})
                    .get("end", {})
                    .get("dateTime", ""),
                    "timeZone": suggestion.get("meetingTimeSlot", {})
                    .get("start", {})
                    .get("timeZone", time_zone),
                },
            }
            formatted_result["meetingTimeSuggestions"].append(formatted_suggestion)

        return formatted_result

    except requests.RequestException as e:
        raise CalendarError(f"Network error while finding meeting times: {str(e)}")


def create_recurring_event(
    current_user: str,
    title: str,
    start_time: str,
    end_time: str,
    description: str,
    recurrence_pattern: Dict,
    access_token: str = None,
    time_zone: str = "Central Standard Time",
) -> Dict:
    """
    Create a recurring event with specified pattern.

    Args:
        current_user: The user's identifier
        title: Event title
        start_time: Start time in ISO format
        end_time: End time in ISO format
        description: Event description
        recurrence_pattern: Dict containing recurrence rules, e.g.:
            {
                "pattern": {
                    "type": "weekly",
                    "interval": 1,
                    "daysOfWeek": ["monday", "wednesday"]
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2024-01-01",
                    "endDate": "2024-12-31"
                }
            }
        time_zone: Time zone for the event times (Windows format, e.g., 'Central Standard Time')

    Returns:
        Created recurring event details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        event_body = {
            "subject": title,
            "body": {"contentType": "text", "content": description},
            "start": {"dateTime": start_time, "timeZone": time_zone},
            "end": {"dateTime": end_time, "timeZone": time_zone},
            "recurrence": recurrence_pattern,
        }

        url = f"{GRAPH_ENDPOINT}/me/events"
        response = session.post(url, json=event_body)

        if not response.ok:
            handle_graph_error(response)

        return format_event(response.json())

    except requests.RequestException as e:
        raise CalendarError(f"Network error while creating recurring event: {str(e)}")


def update_recurring_event(
    current_user: str,
    event_id: str,
    updated_fields: Dict,
    update_type: str = "series",
    access_token: str = None,
) -> Dict:
    """
    Update a recurring event (series or single occurrence).

    Args:
        current_user: The user's identifier
        event_id: Event ID
        updated_fields: Fields to update
        update_type: 'series' or 'occurrence'

    Returns:
        Updated event details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # For single occurrence, use the occurrence ID directly
        # For series, append /series to the endpoint
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"
        if update_type == "series":
            url += "/series"

        response = session.patch(url, json=updated_fields)

        if not response.ok:
            handle_graph_error(response)

        return format_event(response.json())

    except requests.RequestException as e:
        raise CalendarError(f"Network error while updating recurring event: {str(e)}")


def add_attachment(
    current_user: str,
    event_id: str,
    file_name: str,
    content_bytes: bytes,
    content_type: str,
    access_token: str = None,
) -> Dict:
    """
    Add a file attachment to an event.

    Args:
        current_user: The user's identifier
        event_id: Event ID
        file_name: Name of the file
        content_bytes: File content as bytes
        content_type: MIME type of the file

    Returns:
        Attachment details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}/attachments"

        attachment_body = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": file_name,
            "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
            "contentType": content_type,
        }

        response = session.post(url, json=attachment_body)

        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise CalendarError(f"Network error while adding attachment: {str(e)}")


def get_attachments(current_user: str, event_id: str, access_token: str) -> List[Dict]:
    """
    Get all attachments for an event.

    Args:
        current_user: The user's identifier
        event_id: Event ID

    Returns:
        List of attachment details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}/attachments"

        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return response.json().get("value", [])

    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching attachments: {str(e)}")


def delete_attachment(
    current_user: str, event_id: str, attachment_id: str, access_token: str
) -> Dict:
    """
    Delete an attachment from an event.

    Args:
        current_user: The user's identifier
        event_id: Event ID
        attachment_id: Attachment ID

    Returns:
        Status dictionary
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}/attachments/{attachment_id}"

        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": attachment_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting attachment: {str(e)}")


def get_calendar_permissions(
    current_user: str, calendar_id: str, access_token: str
) -> List[Dict]:
    """
    Get sharing permissions for a calendar.

    Args:
        current_user: The user's identifier
        calendar_id: Calendar ID

    Returns:
        List of permission details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/calendarPermissions"

        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        return response.json().get("value", [])

    except requests.RequestException as e:
        raise CalendarError(
            f"Network error while fetching calendar permissions: {str(e)}"
        )


def share_calendar(
    current_user: str,
    calendar_id: str,
    user_email: str,
    role: str = "read",
    access_token: str = None,
) -> Dict:
    """
    Share a calendar with another user.

    Args:
        current_user: The user's identifier
        calendar_id: Calendar ID
        user_email: Email of the user to share with
        role: Permission level ('freeBusyRead', 'limitedRead', 'read')

    Returns:
        Permission details
    """
    role_map = {"freeBusyRead": "freeBusyRead", "limitedRead": "limitedRead", "read": "read"}

    if role not in role_map:
        raise ValueError(f"Invalid role. Must be one of: {list(role_map.keys())}")

    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/calendarPermissions"

        permission_body = {
            "emailAddress": {"address": user_email},
            "role": role_map[role],
        }

        response = session.post(url, json=permission_body)

        if not response.ok:
            handle_graph_error(response)

        return response.json()

    except requests.RequestException as e:
        raise CalendarError(f"Network error while sharing calendar: {str(e)}")


def remove_calendar_sharing(
    current_user: str, calendar_id: str, permission_id: str, access_token: str
) -> Dict:
    """
    Remove sharing permission for a calendar.

    Args:
        current_user: The user's identifier
        calendar_id: Calendar ID
        permission_id: Permission ID to remove

    Returns:
        Status dictionary
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/calendarPermissions/{permission_id}"

        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": permission_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise CalendarError(f"Network error while removing calendar sharing: {str(e)}")


def list_calendar_events(
    current_user: str, calendar_id: str, access_token: str, user_timezone: str = "UTC"
) -> List[Dict]:
    """
    List events for a given calendar.

    Args:
        current_user: User identifier
        calendar_id: ID of the calendar to list events from
        access_token: Optional access token
        user_timezone: User's preferred timezone (Windows format)

    Returns:
        List of formatted event details from the specified calendar

    Raises:
        CalendarError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/events"

        # Add timezone preference header to get events in user's timezone
        headers = {}
        if user_timezone and user_timezone != "UTC":
            headers["Prefer"] = f'outlook.timezone="{user_timezone}"'

        all_events = []
        while url:
            response = session.get(url, headers=headers)
            if not response.ok:
                handle_graph_error(response)
            data = response.json()
            events = data.get("value", [])
            all_events.extend([format_event(evt) for evt in events])
            url = data.get("@odata.nextLink")  # Continue if there's pagination
        return all_events
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching calendar events: {str(e)}")


def check_event_conflicts(
    current_user: str,
    proposed_start_time: str,
    proposed_end_time: str,
    return_conflicting_events: bool = False,
    calendar_ids: List[str] = None,
    check_all_calendars: bool = False,
    time_zone: str = "Central Standard Time",
    access_token: str = None,
) -> Dict:
    """
    Checks for scheduling conflicts within a time window across one or more calendars.

    Args:
        current_user: The current user making the request
        proposed_start_time: Start time of the proposed event (ISO format)
        proposed_end_time: End time of the proposed event (ISO format)
        return_conflicting_events: Whether to include details of conflicting events
        calendar_ids: Optional list of calendar IDs to check (default: uses default calendar)
        check_all_calendars: If True, checks all calendars the user has access to
        time_zone: Time zone for the event times (Windows format, e.g., 'Central Standard Time')
        access_token: Optional access token

    Returns:
        Dictionary with conflict status and optional conflicting event details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)

        # Determine which calendars to check
        calendars_to_check = []
        if check_all_calendars:
            # Get all calendars the user has access to
            url = f"{GRAPH_ENDPOINT}/me/calendars"
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)

            calendars_data = response.json()
            calendars_to_check = [cal["id"] for cal in calendars_data.get("value", [])]
        elif calendar_ids:
            calendars_to_check = calendar_ids
        else:
            # Default to primary calendar
            url = f"{GRAPH_ENDPOINT}/me/calendar"
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)

            calendars_to_check = [response.json().get("id")]

        all_conflicts = []
        conflicts_by_calendar = {}

        # Check each calendar for conflicts
        for calendar_id in calendars_to_check:
            try:
                # Use calendarView to get events in the time range
                url = (
                    f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/calendarView?"
                    f"startDateTime={proposed_start_time}&endDateTime={proposed_end_time}"
                )

                response = session.get(url)
                if not response.ok:
                    handle_graph_error(response)

                events_data = response.json()
                conflicts = events_data.get("value", [])

                if conflicts:
                    # Get calendar name for better reporting
                    try:
                        cal_url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}"
                        cal_response = session.get(cal_url)
                        if cal_response.ok:
                            cal_info = cal_response.json()
                            calendar_name = cal_info.get("name", calendar_id)
                        else:
                            calendar_name = calendar_id
                    except:
                        calendar_name = calendar_id

                    formatted_conflicts = [
                        {
                            "id": event["id"],
                            "title": event.get("subject", "Untitled Event"),
                            "start": event.get("start", {}).get("dateTime", ""),
                            "end": event.get("end", {}).get("dateTime", ""),
                            "calendar_id": calendar_id,
                            "calendar_name": calendar_name,
                        }
                        for event in conflicts
                    ]

                    all_conflicts.extend(formatted_conflicts)
                    conflicts_by_calendar[calendar_id] = {
                        "calendar_name": calendar_name,
                        "conflicts": formatted_conflicts,
                    }
            except Exception as e:
                # If we can't access a calendar, skip it but log the error
                print(f"Error checking calendar {calendar_id}: {str(e)}")
                continue

        has_conflict = len(all_conflicts) > 0

        result = {
            "conflict": has_conflict,
            "calendars_checked": len(calendars_to_check),
        }

        if has_conflict:
            result["conflict_count"] = len(all_conflicts)

            if return_conflicting_events:
                result["conflicting_events"] = all_conflicts
                result["conflicts_by_calendar"] = conflicts_by_calendar

        return result

    except requests.RequestException as e:
        raise CalendarError(f"Network error while checking event conflicts: {str(e)}")
