from googleapiclient.discovery import build
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta, timezone

integration_name = "google_calendar"

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

def create_event(current_user, title, start_time, end_time, description):
    service = get_calendar_service(current_user)
    event = {
        'summary': title,
        'description': description,
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
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
    events_result = service.events().list(calendarId='primary', timeMin=start_date,
                                          timeMax=end_date, singleEvents=True,
                                          orderBy='startTime').execute()
    return [format_event(event, include_description, include_attendees, include_location)
            for event in events_result.get('items', [])]

def get_events_for_date(current_user, date, include_description=False, include_attendees=False, include_location=False):
    service = get_calendar_service(current_user)
    start_of_day = date + 'T00:00:00Z'
    end_of_day = date + 'T23:59:59Z'
    events_result = service.events().list(calendarId='primary', timeMin=start_of_day,
                                          timeMax=end_of_day, singleEvents=True,
                                          orderBy='startTime').execute()
    return [format_event(event, include_description, include_attendees, include_location)
            for event in events_result.get('items', [])]


def get_free_time_slots(current_user, start_date, end_date, duration):
    service = get_calendar_service(current_user)
    # Get all events within the specified date range
    events = get_events_between_dates(current_user, start_date, end_date)

    free_slots = []
    # Convert start_date and end_date to datetime objects
    current_time = datetime.fromisoformat(start_date[:-1])  # Remove 'Z' from ISO format
    end_time = datetime.fromisoformat(end_date[:-1])

    for event in events:
        # Convert event start time to datetime object
        event_start = datetime.fromisoformat(event['start']['dateTime'][:-1])
        # Check if there's enough time between current_time and event_start for the desired duration
        if int((event_start - current_time).total_seconds()) >= duration * 60:
            # If so, add this time slot to free_slots
            free_slots.append({
                'start': current_time.isoformat() + 'Z',
                'end': (event_start - timedelta(minutes=1)).isoformat() + 'Z'
            })
        # Move current_time to the end of this event
        current_time = datetime.fromisoformat(event['end']['dateTime'][:-1])

    # Check if there's a free slot after the last event
    if (end_time - current_time).total_seconds() >= duration * 60:
        free_slots.append({
            'start': current_time.isoformat() + 'Z',
            'end': end_time.isoformat() + 'Z'
        })

    return free_slots


def get_calendar_service(current_user):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build('calendar', 'v3', credentials=credentials)