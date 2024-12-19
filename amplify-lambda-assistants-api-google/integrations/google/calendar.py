from googleapiclient.discovery import build
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials
from datetime import datetime

integration_name = "google_calendar"

def create_event(current_user, title, start_time, end_time, description):
    service = get_calendar_service(current_user)
    event = {
        'summary': title,
        'description': description,
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
    }
    return service.events().insert(calendarId='primary', body=event).execute()

def update_event(current_user, event_id, updated_fields):
    service = get_calendar_service(current_user)
    event = service.events().get(calendarId='primary', eventId=event_id).execute()
    event.update(updated_fields)
    return service.events().update(calendarId='primary', eventId=event_id, body=event).execute()

def delete_event(current_user, event_id):
    service = get_calendar_service(current_user)
    return service.events().delete(calendarId='primary', eventId=event_id).execute()

def get_event_details(current_user, event_id):
    service = get_calendar_service(current_user)
    return service.events().get(calendarId='primary', eventId=event_id).execute()

def get_events_between_dates(current_user, start_date, end_date):
    service = get_calendar_service(current_user)
    events_result = service.events().list(calendarId='primary', timeMin=start_date,
                                          timeMax=end_date, singleEvents=True,
                                          orderBy='startTime').execute()
    return events_result.get('items', [])

def get_events_for_date(current_user, date):
    service = get_calendar_service(current_user)
    start_of_day = date + 'T00:00:00Z'
    end_of_day = date + 'T23:59:59Z'
    events_result = service.events().list(calendarId='primary', timeMin=start_of_day,
                                          timeMax=end_of_day, singleEvents=True,
                                          orderBy='startTime').execute()
    return events_result.get('items', [])

def get_upcoming_events(current_user, end_date):
    service = get_calendar_service(current_user)
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(calendarId='primary', timeMin=now,
                                          timeMax=end_date, singleEvents=True,
                                          orderBy='startTime').execute()
    return events_result.get('items', [])

def get_free_time_slots(current_user, start_date, end_date, duration):
    service = get_calendar_service(current_user)
    # Get all events within the specified date range
    events = get_events_between_dates(current_user, start_date, end_date)

    free_slots = []
    # Convert start_date and end_date to datetime objects
    current_time = datetime.datetime.fromisoformat(start_date[:-1])  # Remove 'Z' from ISO format
    end_time = datetime.datetime.fromisoformat(end_date[:-1])

    for event in events:
        # Convert event start time to datetime object
        event_start = datetime.datetime.fromisoformat(event['start']['dateTime'][:-1])
        # Check if there's enough time between current_time and event_start for the desired duration
        if (event_start - current_time).total_seconds() >= duration * 60:
            # If so, add this time slot to free_slots
            free_slots.append({
                'start': current_time.isoformat() + 'Z',
                'end': (event_start - datetime.timedelta(minutes=1)).isoformat() + 'Z'
            })
        # Move current_time to the end of this event
        current_time = datetime.datetime.fromisoformat(event['end']['dateTime'][:-1])

    # Check if there's a free slot after the last event
    if (end_time - current_time).total_seconds() >= duration * 60:
        free_slots.append({
            'start': current_time.isoformat() + 'Z',
            'end': end_time.isoformat() + 'Z'
        })

    return free_slots

def check_event_conflicts(current_user, proposed_start_time, proposed_end_time):
    service = get_calendar_service(current_user)
    events_result = service.events().list(calendarId='primary', timeMin=proposed_start_time,
                                          timeMax=proposed_end_time, singleEvents=True).execute()
    return len(events_result.get('items', [])) > 0

def get_calendar_service(current_user):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build('calendar', 'v3', credentials=credentials)