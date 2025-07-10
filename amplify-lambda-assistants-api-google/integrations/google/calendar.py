from googleapiclient.discovery import build
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta, timezone
from dateutil import tz
from zoneinfo import ZoneInfo
import re

integration_name = "google_calendar"


def validate_email(email):
    """
    Validates if a given input is potentially a valid email address.

    Args:
        email: The email address to validate

    Returns:
        bool: True if validation passes

    Raises:
        ValueError: If email is invalid, with a descriptive message of why
    """
    if not isinstance(email, str):
        raise ValueError(
            f"Invalid email '{email}': must be a string, got {type(email).__name__}"
        )

    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    if not email_pattern.match(email):
        raise ValueError(
            f"Invalid email '{email}': must match pattern 'text@domain.tld'. "
            "Email should contain a single @ symbol and at least one dot in the domain."
        )
    return True


def format_event(
    event, include_description=False, include_attendees=False, include_location=False
):
    formatted_event = {
        "id": event["id"],
        "title": event["summary"],
        "start": event["start"].get("dateTime", event["start"].get("date")),
        "end": event["end"].get("dateTime", event["end"].get("date")),
    }
    if include_description:
        formatted_event["description"] = event.get("description", "")
    if include_attendees:
        formatted_event["attendees"] = [
            attendee["email"] for attendee in event.get("attendees", [])
        ]
    if include_location:
        formatted_event["location"] = event.get("location", "")
    return formatted_event


def create_event(
    current_user,
    title,
    start_time,
    end_time,
    description=None,
    location=None,
    attendees=None,
    calendar_id="primary",
    conference_data=None,
    recurrence_pattern=None,
    reminders=None,
    send_notifications=True,
    send_updates=None,
    access_token=None,
):
    """
    Creates an event in the user's calendar (single or recurring).

    Args:
        current_user: The current user making the request
        title: Title/summary of the event
        start_time: Start time of the event (ISO format)
        end_time: End time of the event (ISO format)
        description: Optional event description
        location: Optional physical location for in-person meetings
        attendees: Optional list of attendee emails
        calendar_id: Calendar ID (defaults to primary)
        conference_data: Optional video conferencing details (for virtual meetings)
        recurrence_pattern: Optional list of RRULE strings for recurring events
        reminders: Optional list of dictionaries with 'method' and 'minutes' keys
                  (e.g., [{'method': 'email', 'minutes': 30}, {'method': 'popup', 'minutes': 10}])
        sendNotifications: Whether to send notifications to attendees as boolean
        send_updates: Optional string controlling notification behavior ('all', 'externalOnly', or 'none')
        access_token: Optional access token

    Returns:
        Dictionary containing the created event details
    """

    service = get_calendar_service(current_user, access_token)
    event = {
        "summary": title,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }

    # Add optional fields if provided
    if description:
        event["description"] = description

    if location:
        event["location"] = location

    if attendees:
        event["attendees"] = [
            {"email": email} for email in attendees if validate_email(email)
        ]

    if recurrence_pattern:
        event["recurrence"] = recurrence_pattern

    if conference_data:
        event["conferenceData"] = conference_data

    # Add reminders if provided
    if reminders:
        event["reminders"] = {"useDefault": False, "overrides": reminders}

    if not calendar_id:
        calendar_id = "primary"
    # Create the event with the sendUpdates parameter (if provided)
    kwargs = {"calendarId": calendar_id, "body": event}
    if conference_data:
        kwargs["conferenceDataVersion"] = 1

    if send_notifications:
        kwargs["sendUpdates"] = send_updates or "all"
    else:
        kwargs["sendUpdates"] = "none"

    event_result = service.events().insert(**kwargs).execute()

    return event_result


def update_event(current_user, event_id, updated_fields, access_token=None):
    service = get_calendar_service(current_user, access_token)
    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    event.update(updated_fields)
    updated_event = (
        service.events()
        .update(calendarId="primary", eventId=event_id, body=event)
        .execute()
    )
    return {"id": updated_event["id"], "title": updated_event["summary"]}


def delete_event(current_user, event_id, access_token=None):
    service = get_calendar_service(current_user, access_token)
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return {"status": "deleted", "id": event_id}


def check_event_conflicts(
    current_user,
    proposed_start_time,
    proposed_end_time,
    return_conflicting_events=False,
    calendar_ids=None,
    check_all_calendars=False,
    access_token=None,
):
    """
    Checks for scheduling conflicts within a time window across one or more calendars.

    Args:
        current_user: The current user making the request
        proposed_start_time: Start time of the proposed event (ISO format)
        proposed_end_time: End time of the proposed event (ISO format)
        return_conflicting_events: Whether to include details of conflicting events
        calendar_ids: Optional list of calendar IDs to check (default: ['primary'])
        check_all_calendars: If True, checks all calendars the user has access to
        access_token: Optional access token

    Returns:
        Dictionary with conflict status and optional conflicting event details
    """
    service = get_calendar_service(current_user, access_token)

    # Determine which calendars to check
    calendars_to_check = []
    if check_all_calendars:
        # Get all calendars the user has access to
        calendar_list = service.calendarList().list().execute()
        calendars_to_check = [cal["id"] for cal in calendar_list.get("items", [])]
    elif calendar_ids:
        calendars_to_check = calendar_ids
    else:
        # Default to primary calendar only
        calendars_to_check = ["primary"]

    all_conflicts = []
    conflicts_by_calendar = {}

    # Check each calendar for conflicts
    for calendar_id in calendars_to_check:
        try:
            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=proposed_start_time,
                    timeMax=proposed_end_time,
                    singleEvents=True,
                )
                .execute()
            )

            conflicts = events_result.get("items", [])

            if conflicts:
                # Get calendar name for better reporting
                try:
                    cal_info = service.calendars().get(calendarId=calendar_id).execute()
                    calendar_name = cal_info.get("summary", calendar_id)
                except:
                    calendar_name = calendar_id

                formatted_conflicts = [
                    {
                        "id": event["id"],
                        "title": event["summary"],
                        "start": event["start"].get(
                            "dateTime", event["start"].get("date")
                        ),
                        "end": event["end"].get("dateTime", event["end"].get("date")),
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

    result = {"conflict": has_conflict, "calendars_checked": len(calendars_to_check)}

    if has_conflict:
        result["conflict_count"] = len(all_conflicts)

        if return_conflicting_events:
            result["conflicting_events"] = all_conflicts
            result["conflicts_by_calendar"] = conflicts_by_calendar

    return result


def get_event_details(current_user, event_id, access_token=None):
    service = get_calendar_service(current_user, access_token)
    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    return format_event(
        event, include_description=True, include_attendees=True, include_location=True
    )


def get_events_between_dates(
    current_user,
    start_date,
    end_date,
    include_description=False,
    include_attendees=False,
    include_location=False,
    access_token=None,
):
    service = get_calendar_service(current_user, access_token)
    start_date = normalize_date(start_date)
    end_date = normalize_date(end_date)
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [
        format_event(event, include_description, include_attendees, include_location)
        for event in events_result.get("items", [])
    ]


def get_events_for_date(
    current_user,
    date,
    include_description=False,
    include_attendees=False,
    include_location=False,
    access_token=None,
):
    service = get_calendar_service(current_user, access_token)

    # Normalize the input date
    normalized_date = normalize_date(date)
    if normalized_date is None:
        raise ValueError("The provided date format is invalid.")

    # Extract the date part (YYYY-MM-DD) from the normalized date
    date_only = normalized_date.split("T")[0]  # Get YYYY-MM-DD

    start_of_day = date_only + "T00:00:00Z"
    end_of_day = date_only + "T23:59:59Z"

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_of_day,
            timeMax=end_of_day,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    return [
        format_event(event, include_description, include_attendees, include_location)
        for event in events_result.get("items", [])
    ]


def normalize_date(date_string):
    # Define the target format
    target_format = "%Y-%m-%dT%H:%M:%SZ"

    # Try parsing in various formats, including variations
    formats_to_try = [
        "%Y-%m-%d",  # Original format
        "%Y-%m-%dT%H:%M:%SZ",  # Full UTC format
        "%Y-%m-%dT%H:%M:%S",  # Local time without Z
        "%Y-%m-%dT%H:%M:%S.%fZ",  # With milliseconds
        "%Y-%m-%dT%H:%M:%S.%f",  # Local time with milliseconds
        "%d-%m-%Y",  # Day-Month-Year
        "%m/%d/%Y",  # Month/Day/Year
        "%Y/%m/%d",  # Year/Month/Day
        "%d/%m/%Y",  # Day/Month/Year
        "%m/%d/%y",  # Month/Day/Two-digit Year
        "%Y-%m-%dT%H:%M:%S.%fZ",  # Full UTC with fractional seconds
        "%Y-%m-%dT%H:%M:%SZ",  # ISO-like formats
        "%Y-%m-%dT%H:%M:%S%z",  # ISO format with timezone offset
    ]

    for date_format in formats_to_try:
        try:
            # Attempt to parse the date
            parsed_date = datetime.strptime(date_string, date_format)
            # Format to the desired format with time zone
            return parsed_date.strftime(target_format)
        except ValueError:
            continue

    # Check for ISO 8601 formats with timezone offset
    try:
        parsed_date = datetime.fromisoformat(
            date_string.replace("Z", "+00:00")
        )  # Replace Z with equivalent
        return parsed_date.strftime(target_format)
    except ValueError:
        pass

    print(f"Could not parse date {date_string}")
    # If no formats were valid, return None or raise an exception
    raise ValueError(
        f"Could not parse date {date_string}. Please use %Y-%m-%dT%H:%M:%SZ format."
    )


def get_free_time_slots(
    current_user,
    start_date,
    end_date,
    duration,
    user_time_zone="America/Chicago",
    include_weekends=False,
    allowed_time_windows=["08:00-17:00"],
    exclude_dates=None,
    access_token=None,
):
    if user_time_zone is None:
        user_time_zone = "America/Chicago"

    allowed_time_windows = allowed_time_windows or ["08:00-17:00"]

    # service = get_calendar_service(current_user, access_token)
    events = get_events_between_dates(
        current_user, start_date, end_date, access_token=access_token
    )

    print(f"User time zone: {user_time_zone}")

    free_slots_by_date = {}
    usertz = ZoneInfo(user_time_zone)

    # Parse start and end times
    current_date = parse_datetime(start_date, usertz).date()
    end_date = parse_datetime(end_date, usertz).date()

    # Convert allowed_time_windows to time objects
    time_windows = []
    for window in allowed_time_windows:
        start_str, end_str = window.split("-")
        start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
        end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
        time_windows.append((start_time, end_time))

    # Convert exclude_dates to datetime objects in user timezone
    excluded_dates = set()
    if exclude_dates:
        for date_str in exclude_dates:
            dt = datetime.fromisoformat(date_str).date()
            excluded_dates.add(dt)

    free_slots_by_date = {}
    sorted_events = sorted(events, key=lambda x: parse_datetime(x["start"], usertz))

    while current_date <= end_date:
        # Skip if it's a weekend and weekends aren't included
        if not include_weekends and current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        # Skip if date is in excluded dates
        if current_date in excluded_dates:
            current_date += timedelta(days=1)
            continue

        # Get events for current date
        days_events = [
            event
            for event in sorted_events
            if parse_datetime(event["start"], usertz).date() <= current_date
            and parse_datetime(event["end"], usertz).date() >= current_date
        ]

        # Process each time window for the current date
        for window_start, window_end in time_windows:
            window_start_dt = datetime.combine(
                current_date, window_start, tzinfo=usertz
            )
            window_end_dt = datetime.combine(current_date, window_end, tzinfo=usertz)

            current_time = window_start_dt

            # Find any events that overlap with this window
            for event in days_events:
                event_start = parse_datetime(event["start"], usertz)
                event_end = parse_datetime(event["end"], usertz)

                # Adjust event times to window boundaries if they extend beyond
                if event_start.date() < current_date:
                    event_start = window_start_dt
                if event_end.date() > current_date:
                    event_end = window_end_dt

                # If event starts after window end or ends before window start, skip
                if event_start >= window_end_dt or event_end <= window_start_dt:
                    continue

                # Add free slot before event if there's enough time
                if (
                    event_start > current_time
                    and (event_start - current_time).total_seconds() >= duration * 60
                ):
                    add_time_slot(free_slots_by_date, current_time, event_start, usertz)

                current_time = max(current_time, event_end)

            # Add remaining time in window if there's enough
            if (
                current_time < window_end_dt
                and (window_end_dt - current_time).total_seconds() >= duration * 60
            ):
                add_time_slot(free_slots_by_date, current_time, window_end_dt, usertz)

        current_date += timedelta(days=1)

    return [
        {"date": date, "times": times}
        for date, times in sorted(free_slots_by_date.items())
    ]


# def get_free_time_slots(current_user, start_date, end_date, duration, user_time_zone='America/Chicago'):
#     if user_time_zone is None:
#         user_time_zone = 'America/Chicago'
#
#     service = get_calendar_service(current_user)
#     events = get_events_between_dates(current_user, start_date, end_date)
#
#     print(f"User time zone: {user_time_zone}")
#
#     free_slots_by_date = {}
#     usertz = ZoneInfo(user_time_zone)
#
#     def parse_datetime(date_str):
#         dt = datetime.fromisoformat(date_str)
#         if dt.tzinfo is None:
#             dt = dt.replace(tzinfo=usertz)
#         return dt.astimezone(usertz)
#
#     # Parse start and end times
#     current_time = parse_datetime(start_date)
#     end_time = parse_datetime(end_date)
#
#     # Sort events by start time
#     sorted_events = sorted(events, key=lambda x: parse_datetime(x['start']))
#
#     # Process events
#     for event in sorted_events:
#         event_start = parse_datetime(event['start'])
#
#         # Check if there's enough time before the event
#         if (event_start - current_time).total_seconds() >= duration * 60:
#             add_time_slot(free_slots_by_date, current_time, event_start, usertz)
#
#         # Move current time to end of event
#         event_end = parse_datetime(event['end'])
#         current_time = max(current_time, event_end)
#
#     # Add final slot if there's time remaining
#     if (end_time - current_time).total_seconds() >= duration * 60:
#         add_time_slot(free_slots_by_date, current_time, end_time, usertz)
#
#     # Format results
#     result = [
#         {"date": date, "times": times}
#         for date, times in sorted(free_slots_by_date.items())
#     ]
#
#     return result


def add_time_slot(slots_dict, start_time, end_time, timezone):
    date_str = start_time.strftime("%m/%d/%y")
    time_str = (
        f"{start_time.strftime('%I:%M %p')} – "
        f"{(end_time - timedelta(minutes=1)).strftime('%I:%M %p')} "
        f"({start_time.tzname()})"
    )

    if date_str not in slots_dict:
        slots_dict[date_str] = []
    slots_dict[date_str].append(time_str)


def timezone_abbreviation(timezone_str):
    try:
        timezone = tz.gettz(timezone_str)
        current_time = datetime.now(timezone)
        return current_time.tzname() or timezone_str
    except Exception:
        # Fallback to original timezone string if anything fails
        return timezone_str.split("/")[-1]


def parse_datetime(date_str, usertz):
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        # If no timezone info, assume it's in user's timezone
        dt = dt.replace(tzinfo=usertz)
    else:
        # If it has timezone info, convert to user's timezone
        dt = dt.astimezone(usertz)
    return dt


def get_calendar_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("calendar", "v3", credentials=credentials)


def list_calendars(current_user, access_token=None, include_shared=False):
    """
    Lists all calendars the user has access to.

    Args:
        current_user: The current user making the request
        access_token: Optional access token
        include_shared: Whether to include calendars shared with the user (default: True)

    Returns:
        List of calendar objects with id, summary, description, etc.
    """
    service = get_calendar_service(current_user, access_token)
    calendar_list = service.calendarList().list().execute()

    calendars = []
    for calendar in calendar_list.get("items", []):
        if calendar["id"] == "en.usa#holiday@group.v.calendar.google.com":
            continue
        # Determine if calendar is shared or owned
        is_owned = calendar.get("accessRole", "") == "owner"
        is_primary = calendar.get("primary", False)
        is_shared = not is_primary and not is_owned

        # Skip shared calendars if include_shared is False
        if is_shared and not include_shared:
            continue

        calendars.append(
            {
                "id": calendar["id"],
                "name": calendar.get("summary", ""),
                "description": calendar.get("description", ""),
                "primary": is_primary,
                "accessRole": calendar.get("accessRole", ""),
                "isShared": is_shared,
                "isOwned": is_owned,
                "owner": (
                    calendar.get("owner", {}).get("displayName", "")
                    if is_shared
                    else ""
                ),
                "ownerEmail": (
                    calendar.get("owner", {}).get("email", "") if is_shared else ""
                ),
            }
        )

    return calendars


def create_calendar(
    current_user, name, description=None, timezone=None, access_token=None
):
    """
    Creates a new calendar for the user.

    Args:
        current_user: The current user making the request
        name: Name/summary of the calendar
        description: Optional description for the calendar
        timezone: Optional timezone for the calendar (defaults to user's timezone)
        access_token: Optional access token

    Returns:
        Dictionary containing the created calendar details
    """
    service = get_calendar_service(current_user, access_token)

    calendar_body = {"summary": name}

    if description:
        calendar_body["description"] = description

    if timezone:
        calendar_body["timeZone"] = timezone

    created_calendar = service.calendars().insert(body=calendar_body).execute()

    return {
        "id": created_calendar["id"],
        "name": created_calendar.get("summary", ""),
        "description": created_calendar.get("description", ""),
        "timezone": created_calendar.get("timeZone", ""),
    }


def delete_calendar(current_user, calendar_id, access_token=None):
    """
    Deletes a calendar.

    Args:
        current_user: The current user making the request
        calendar_id: ID of the calendar to delete
        access_token: Optional access token

    Returns:
        Dictionary indicating success status
    """
    service = get_calendar_service(current_user, access_token)

    # Prevent accidental deletion of primary calendar
    if calendar_id.lower() == "primary":
        raise ValueError("Cannot delete primary calendar")

    service.calendars().delete(calendarId=calendar_id).execute()
    return {"status": "deleted", "id": calendar_id}


def update_calendar_permissions(
    current_user,
    calendar_id,
    email,
    role="reader",
    send_notification=False,
    notification_message=None,
    access_token=None,
):
    """
    Shares a calendar with another user by setting permissions.

    Args:
        current_user: The current user making the request
        calendar_id: ID of the calendar to share
        email: Email address of the user to share with
        role: Permission role ('none', 'freeBusyReader', 'reader', 'writer', 'owner')
        send_notification: Whether to send notification email
        notification_message: Optional custom message for notification
        access_token: Optional access token

    Returns:
        Dictionary containing the updated access control rule
    """
    # Validate email
    validate_email(email)

    # Validate role
    allowed_roles = ["none", "freeBusyReader", "reader", "writer", "owner"]
    if role not in allowed_roles:
        raise ValueError(f"Role must be one of {', '.join(allowed_roles)}")

    service = get_calendar_service(current_user, access_token)

    rule_body = {"scope": {"type": "user", "value": email}, "role": role}
    if not calendar_id:
        calendar_id = "primary"

    acl = (
        service.acl()
        .insert(
            calendarId=calendar_id, body=rule_body, sendNotifications=send_notification
        )
        .execute()
    )

    return {"id": acl["id"], "email": email, "role": acl["role"], "status": "added"}


def create_recurring_event(
    current_user,
    title,
    start_time,
    end_time,
    description=None,
    location=None,
    attendees=None,
    recurrence_pattern=None,
    calendar_id="primary",
    access_token=None,
):
    """
    Creates a recurring event in the user's calendar.

    Args:
        current_user: The current user making the request
        title: Title/summary of the event
        start_time: Start time of the event (ISO format)
        end_time: End time of the event (ISO format)
        description: Optional event description
        location: Optional event location
        attendees: Optional list of attendee emails
        recurrence_pattern: List of RRULE strings (e.g., ['RRULE:FREQ=WEEKLY;COUNT=10'])
        calendar_id: Calendar ID (defaults to primary)
        access_token: Optional access token

    Returns:
        Dictionary containing the created event details
    """
    # Default recurrence if not specified
    if recurrence_pattern is None:
        recurrence_pattern = ["RRULE:FREQ=WEEKLY;COUNT=4"]

    return create_event(
        current_user=current_user,
        title=title,
        start_time=start_time,
        end_time=end_time,
        description=description,
        location=location,
        attendees=attendees,
        calendar_id=calendar_id,
        recurrence_pattern=recurrence_pattern,
        access_token=access_token,
    )


def add_event_reminders(
    current_user, event_id, reminders=None, calendar_id="primary", access_token=None
):
    """
    Adds reminders to an existing calendar event.

    Args:
        current_user: The current user making the request
        event_id: ID of the event to update
        reminders: List of dictionaries with 'method' and 'minutes' keys
                  (e.g., [{'method': 'email', 'minutes': 30}, {'method': 'popup', 'minutes': 10}])
        calendar_id: Calendar ID (defaults to primary)
        access_token: Optional access token

    Returns:
        Dictionary containing the updated event details
    """
    service = get_calendar_service(current_user, access_token)

    # Get the existing event
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    # Default reminders if not specified
    if reminders is None:
        reminders = [{"method": "popup", "minutes": 10}]

    # Update the reminders
    event["reminders"] = {"useDefault": False, "overrides": reminders}
    if not calendar_id:
        calendar_id = "primary"

    updated_event = (
        service.events()
        .update(calendarId=calendar_id, eventId=event_id, body=event)
        .execute()
    )

    # Format the response
    reminder_list = []
    for reminder in reminders:
        reminder_list.append(
            f"{reminder['method']} ({reminder['minutes']} minutes before)"
        )

    return {
        "id": updated_event["id"],
        "title": updated_event["summary"],
        "reminders": reminder_list,
    }


def update_calendar(
    current_user,
    calendar_id,
    name=None,
    description=None,
    timezone=None,
    access_token=None,
):
    """
    Updates an existing calendar's details.

    Args:
        current_user: The current user making the request
        calendar_id: ID of the calendar to update
        name: New name/summary for the calendar (optional)
        description: New description for the calendar (optional)
        timezone: New timezone for the calendar (optional)
        access_token: Optional access token

    Returns:
        Dictionary containing the updated calendar details
    """
    service = get_calendar_service(current_user, access_token)

    if not calendar_id:
        calendar_id = "primary"

    # First get the existing calendar
    calendar = service.calendars().get(calendarId=calendar_id).execute()

    # Update the fields that were provided
    if name:
        calendar["summary"] = name

    if description is not None:  # Allow empty string to clear description
        calendar["description"] = description

    if timezone:
        calendar["timeZone"] = timezone

    # Update the calendar
    updated_calendar = (
        service.calendars().update(calendarId=calendar_id, body=calendar).execute()
    )

    return {
        "id": updated_calendar["id"],
        "name": updated_calendar.get("summary", ""),
        "description": updated_calendar.get("description", ""),
        "timezone": updated_calendar.get("timeZone", ""),
    }


def get_calendar_details(current_user, calendar_id, access_token=None):
    """
    Retrieves detailed information about a specific calendar.

    Args:
        current_user: The current user making the request
        calendar_id: ID of the calendar to retrieve
        access_token: Optional access token

    Returns:
        Dictionary containing the calendar details
    """
    service = get_calendar_service(current_user, access_token)

    if not calendar_id:
        calendar_id = "primary"

    try:
        calendar = service.calendars().get(calendarId=calendar_id).execute()

        # Extract and format the relevant details
        result = {
            "id": calendar["id"],
            "name": calendar.get("summary", ""),
            "description": calendar.get("description", ""),
            "timezone": calendar.get("timeZone", ""),
            "kind": calendar.get("kind", ""),
        }

        # Add location details if available
        if "location" in calendar:
            result["location"] = calendar["location"]

        # Get additional metadata from the calendarList
        try:
            calendar_list_entry = (
                service.calendarList().get(calendarId=calendar_id).execute()
            )

            # Add additional properties from calendarList entry
            result["backgroundColor"] = calendar_list_entry.get("backgroundColor", "")
            result["foregroundColor"] = calendar_list_entry.get("foregroundColor", "")
            result["colorId"] = calendar_list_entry.get("colorId", "")
            result["accessRole"] = calendar_list_entry.get("accessRole", "")
            result["primary"] = calendar_list_entry.get("primary", False)
            result["selected"] = calendar_list_entry.get("selected", False)
            result["hidden"] = calendar_list_entry.get("hidden", False)
        except Exception as e:
            # If we can't get the additional metadata, proceed with the basic info
            pass

        return result

    except Exception as e:
        error_message = str(e)
        if "Not Found" in error_message:
            raise ValueError(f"Calendar not found with ID: {calendar_id}")
        raise Exception(f"Error retrieving calendar details: {error_message}")
