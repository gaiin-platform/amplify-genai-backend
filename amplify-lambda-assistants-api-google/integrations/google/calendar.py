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
        raise ValueError(f"Invalid email '{email}': must be a string, got {type(email).__name__}")

    email_pattern = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    if not email_pattern.match(email):
        raise ValueError(
            f"Invalid email '{email}': must match pattern 'text@domain.tld'. "
            "Email should contain a single @ symbol and at least one dot in the domain."
        )
    return True

def format_event(event, include_description=False, include_attendees=False, include_location=False):
    formatted_event = {
        'id': event['id'],
        'title': event['summary'],
        'start': event['start'].get('dateTime', event['start'].get('date')),
        'end': event['end'].get('dateTime', event['end'].get('date'))
    }
    if include_description:
        formatted_event['description'] = event.get('description', '')
    if include_attendees:
        formatted_event['attendees'] = [attendee['email'] for attendee in event.get('attendees', [])]
    if include_location:
        formatted_event['location'] = event.get('location', '')
    return formatted_event




def create_event(current_user, title, start_time, end_time, description, attendees=None):
    if attendees:
        for email in attendees:
            validate_email(email)

    service = get_calendar_service(current_user)
    event = {
        'summary': title,
        'description': description,
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
        'attendees': [{'email': email} for email in (attendees or [])]
    }
    created_event = service.events().insert(calendarId='primary', body=event).execute()
    return {'id': created_event['id'], 'title': created_event['summary']}


def update_event(current_user, event_id, updated_fields):
    service = get_calendar_service(current_user)
    event = service.events().get(calendarId='primary', eventId=event_id).execute()
    event.update(updated_fields)
    updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
    return {'id': updated_event['id'], 'title': updated_event['summary']}

def delete_event(current_user, event_id):
    service = get_calendar_service(current_user)
    service.events().delete(calendarId='primary', eventId=event_id).execute()
    return {'status': 'deleted', 'id': event_id}

def check_event_conflicts(current_user, proposed_start_time, proposed_end_time, return_conflicting_events=False):
    service = get_calendar_service(current_user)
    events_result = service.events().list(calendarId='primary', timeMin=proposed_start_time,
                                          timeMax=proposed_end_time, singleEvents=True).execute()
    conflicts = events_result.get('items', [])
    has_conflict = len(conflicts) > 0

    result = {'conflict': has_conflict}

    if return_conflicting_events and has_conflict:
        result['conflicting_events'] = [
            {
                'id': event['id'],
                'title': event['summary'],
                'start': event['start'].get('dateTime', event['start'].get('date')),
                'end': event['end'].get('dateTime', event['end'].get('date'))
            }
            for event in conflicts
        ]

    return result

def get_event_details(current_user, event_id):
    service = get_calendar_service(current_user)
    event = service.events().get(calendarId='primary', eventId=event_id).execute()
    return format_event(event, include_description=True, include_attendees=True, include_location=True)

def get_events_between_dates(current_user, start_date, end_date, include_description=False, include_attendees=False, include_location=False):
    service = get_calendar_service(current_user)
    start_date = normalize_date(start_date)
    end_date = normalize_date(end_date)
    events_result = service.events().list(calendarId='primary', timeMin=start_date,
                                          timeMax=end_date, singleEvents=True,
                                          orderBy='startTime').execute()
    return [format_event(event, include_description, include_attendees, include_location)
            for event in events_result.get('items', [])]

def get_events_for_date(current_user, date, include_description=False, include_attendees=False, include_location=False):
    service = get_calendar_service(current_user)

    # Normalize the input date
    normalized_date = normalize_date(date)
    if normalized_date is None:
        raise ValueError("The provided date format is invalid.")

    # Extract the date part (YYYY-MM-DD) from the normalized date
    date_only = normalized_date.split("T")[0]  # Get YYYY-MM-DD

    start_of_day = date_only + 'T00:00:00Z'
    end_of_day = date_only + 'T23:59:59Z'

    events_result = service.events().list(calendarId='primary', timeMin=start_of_day,
                                          timeMax=end_of_day, singleEvents=True,
                                          orderBy='startTime').execute()

    return [format_event(event, include_description, include_attendees, include_location)
            for event in events_result.get('items', [])]


def normalize_date(date_string):
    # Define the target format
    target_format = "%Y-%m-%dT%H:%M:%SZ"

    # Try parsing in various formats, including variations
    formats_to_try = [
        "%Y-%m-%d",                     # Original format
        "%Y-%m-%dT%H:%M:%SZ",           # Full UTC format
        "%Y-%m-%dT%H:%M:%S",            # Local time without Z
        "%Y-%m-%dT%H:%M:%S.%fZ",        # With milliseconds
        "%Y-%m-%dT%H:%M:%S.%f",         # Local time with milliseconds
        "%d-%m-%Y",                     # Day-Month-Year
        "%m/%d/%Y",                     # Month/Day/Year
        "%Y/%m/%d",                     # Year/Month/Day
        "%d/%m/%Y",                     # Day/Month/Year
        "%m/%d/%y",                     # Month/Day/Two-digit Year
        "%Y-%m-%dT%H:%M:%S.%fZ",        # Full UTC with fractional seconds
        "%Y-%m-%dT%H:%M:%SZ",           # ISO-like formats
        "%Y-%m-%dT%H:%M:%S%z",          # ISO format with timezone offset
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
        parsed_date = datetime.fromisoformat(date_string.replace("Z", "+00:00"))  # Replace Z with equivalent
        return parsed_date.strftime(target_format)
    except ValueError:
        pass

    print(f"Could not parse date {date_string}")
    # If no formats were valid, return None or raise an exception
    raise ValueError(f"Could not parse date {date_string}. Please use %Y-%m-%dT%H:%M:%SZ format.")


def get_free_time_slots(current_user, start_date, end_date, duration,
                        user_time_zone='America/Chicago', include_weekends=False,
                        allowed_time_windows=["08:00-17:00"], exclude_dates=None):
    if user_time_zone is None:
        user_time_zone = 'America/Chicago'

    allowed_time_windows = allowed_time_windows or ["08:00-17:00"]

    service = get_calendar_service(current_user)
    events = get_events_between_dates(current_user, start_date, end_date)
    usertz = ZoneInfo(user_time_zone)

    # Parse start and end times
    current_date = parse_datetime(start_date, usertz).date()
    end_date = parse_datetime(end_date, usertz).date()

    # Convert allowed_time_windows to time objects
    time_windows = []
    for window in allowed_time_windows:
        start_str, end_str = window.split('-')
        start_time = datetime.strptime(start_str.strip(), '%H:%M').time()
        end_time = datetime.strptime(end_str.strip(), '%H:%M').time()
        time_windows.append((start_time, end_time))

    # Convert exclude_dates to datetime objects in user timezone
    excluded_dates = set()
    if exclude_dates:
        for date_str in exclude_dates:
            dt = datetime.fromisoformat(date_str).date()
            excluded_dates.add(dt)

    free_slots_by_date = {}
    sorted_events = sorted(events, key=lambda x: parse_datetime(x['start'], usertz))

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
            event for event in sorted_events
            if parse_datetime(event['start'], usertz).date() <= current_date
               and parse_datetime(event['end'], usertz).date() >= current_date
        ]

        # Process each time window for the current date
        for window_start, window_end in time_windows:
            window_start_dt = datetime.combine(current_date, window_start, tzinfo=usertz)
            window_end_dt = datetime.combine(current_date, window_end, tzinfo=usertz)

            current_time = window_start_dt

            # Find any events that overlap with this window
            for event in days_events:
                event_start = parse_datetime(event['start'], usertz)
                event_end = parse_datetime(event['end'], usertz)

                # Adjust event times to window boundaries if they extend beyond
                if event_start.date() < current_date:
                    event_start = window_start_dt
                if event_end.date() > current_date:
                    event_end = window_end_dt

                # If event starts after window end or ends before window start, skip
                if event_start >= window_end_dt or event_end <= window_start_dt:
                    continue

                # Add free slot before event if there's enough time
                if event_start > current_time and (event_start - current_time).total_seconds() >= duration * 60:
                    add_time_slot(free_slots_by_date, current_time, event_start, usertz)

                current_time = max(current_time, event_end)

            # Add remaining time in window if there's enough
            if current_time < window_end_dt and (window_end_dt - current_time).total_seconds() >= duration * 60:
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
    time_str = (f"{start_time.strftime('%I:%M %p')} – "
                f"{(end_time - timedelta(minutes=1)).strftime('%I:%M %p')} "
                f"({start_time.tzname()})")

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
        return timezone_str.split('/')[-1]

def parse_datetime(date_str, usertz):
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        # If no timezone info, assume it's in user's timezone
        dt = dt.replace(tzinfo=usertz)
    else:
        # If it has timezone info, convert to user's timezone
        dt = dt.astimezone(usertz)
    return dt

def get_calendar_service(current_user):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build('calendar', 'v3', credentials=credentials)