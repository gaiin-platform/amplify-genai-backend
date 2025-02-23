import json
import requests
from datetime import datetime, timedelta
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
        error_message = error_data.get('error', {}).get('message', 'Unknown error')
    except json.JSONDecodeError:
        error_message = response.text
    raise CalendarError(f"Graph API error: {error_message} (Status: {response.status_code})")


def create_event(current_user: str, title: str, start_time: str, 
                end_time: str, description: str, access_token: str) -> Dict:
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        # Validate datetime formats
        for dt in [start_time, end_time]:
            try:
                datetime.fromisoformat(dt.replace('Z', '+00:00'))
            except ValueError:
                raise CalendarError(f"Invalid datetime format: {dt}")

        # Graph expects dateTime/timeZone in ISO8601 format. If you're passing
        # '2025-01-30T10:00:00Z', specify timeZone = 'UTC' or the user's zone.
        event_body = {
            "subject": title,
            "body": {
                "contentType": "text",
                "content": description
            },
            "start": {
                "dateTime": start_time,
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_time,
                "timeZone": "UTC"
            }
        }

        url = f"{GRAPH_ENDPOINT}/me/events"
        response = session.post(url, data=json.dumps(event_body))

        if not response.ok:
            handle_graph_error(response)
            
        response.raise_for_status()
        created_event = response.json()

        return format_event(created_event)
    except requests.RequestException as e:
        raise CalendarError(f"Network error while creating event: {str(e)}")

def update_event(current_user, event_id, updated_fields, access_token):

    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"
        
        # Validate datetime formats if present
        for time_field in ['start', 'end']:
            if time_field in updated_fields:
                try:
                    dt = updated_fields[time_field]['dateTime']
                    datetime.fromisoformat(dt.replace('Z', '+00:00'))
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
            return {'status': 'deleted', 'id': event_id}
        
        handle_graph_error(response)
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting event: {str(e)}")


def get_event_details(current_user, event_id, access_token):

    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}"
        response = session.get(url)
        
        if not response.ok:
            handle_graph_error(response)
            
        event = response.json()
        return format_event(event)
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching event: {str(e)}")



def get_events_between_dates(current_user, start_dt, end_dt, page_size: int = 50, access_token: str = None):
    """
    Retrieves events between two date/times, e.g. '2025-01-30T00:00:00Z' to '2025-01-31T23:59:59Z'.
    Uses /calendarView endpoint for easy range queries.
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = (f"{GRAPH_ENDPOINT}/me/calendarView?"
               f"startDateTime={start_dt}&endDateTime={end_dt}"
               f"&$orderby=start/dateTime&$top={page_size}")
        
        all_events = []
        while url:
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)
                
            data = response.json()
            events = data.get('value', [])
            all_events.extend([format_event(evt) for evt in events])
            
            # Handle pagination
            url = data.get('@odata.nextLink')
            
        return all_events
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching events: {str(e)}")



def format_event(event: Dict) -> Dict:
    """
    Format event data consistently.
    
    Args:
        event: Raw event data from Graph API
    
    Returns:
        Dict containing formatted event details
    """
    return {
        'id': event.get('id', ''),
        'subject': event.get('subject', ''),
        'start': event.get('start', {}).get('dateTime', ''),
        'end': event.get('end', {}).get('dateTime', ''),
        'bodyPreview': event.get('bodyPreview', ''),
        'location': event.get('location', {}).get('displayName', ''),
        'organizer': event.get('organizer', {}).get('emailAddress', {}).get('name', ''),
        'status': event.get('showAs', ''),
        'isOnlineMeeting': event.get('isOnlineMeeting', False),
        'onlineMeetingUrl': event.get('onlineMeeting').get('joinUrl', '') if event.get('onlineMeeting') else ''
    }

####### Additions
def list_calendars(current_user: str, access_token: str) -> List[Dict]:
    """
    Retrieve all calendars available in the user's mailbox.
    
    Args:
        current_user: The user's identifier
        
    Returns:
        List of calendar objects with their properties
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars"
        
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
            
        calendars = response.json().get('value', [])
        return [{
            'id': cal['id'],
            'name': cal.get('name', ''),
            'color': cal.get('color', ''),
            'isDefaultCalendar': cal.get('isDefaultCalendar', False),
            'canShare': cal.get('canShare', False),
            'canViewPrivateItems': cal.get('canViewPrivateItems', False),
            'owner': cal.get('owner', {}).get('name', '')
        } for cal in calendars]
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching calendars: {str(e)}")

def create_calendar(current_user: str, name: str, color: Optional[str] = None, access_token: str = None) -> Dict:
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
            return {'status': 'deleted', 'id': calendar_id}
            
        handle_graph_error(response)
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting calendar: {str(e)}")

def respond_to_event(current_user: str, event_id: str, response_type: str, 
                    comment: Optional[str] = None, send_response: bool = True, access_token: str = None) -> Dict:
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
    valid_responses = {'accept', 'decline', 'tentativelyAccept'}
    if response_type not in valid_responses:
        raise ValueError(f"Invalid response_type. Must be one of: {valid_responses}")
        
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/events/{event_id}/{response_type}"
        
        body = {
            "sendResponse": send_response
        }
        if comment:
            body["comment"] = comment
            
        response = session.post(url, json=body)
        if not response.ok:
            handle_graph_error(response)
            
        return {'status': 'success', 'response_type': response_type}
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while responding to event: {str(e)}")

def find_meeting_times(current_user: str, attendees: List[Dict], 
                      duration_minutes: int = 30,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None,
                      access_token: str = None) -> Dict:
    """
    Find available meeting times for a group of attendees.
    
    Args:
        current_user: The user's identifier
        attendees: List of attendee dictionaries with 'email' key
        duration_minutes: Length of the meeting in minutes
        start_time: Optional start time boundary (ISO format)
        end_time: Optional end time boundary (ISO format)
        
    Returns:
        Dictionary containing suggested meeting times
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/users/{current_user}/findMeetingTimes"
        
        # Prepare attendees format
        formatted_attendees = [{"emailAddress": {"address": a["email"]}} for a in attendees]
        
        meeting_body = {
            "attendees": formatted_attendees,
            "timeConstraint": {
                "timeslots": [{
                    "start": {
                        "dateTime": start_time or datetime.now().isoformat(),
                        "timeZone": "UTC"
                    },
                    "end": {
                        "dateTime": end_time or (datetime.now() + timedelta(days=7)).isoformat(),
                        "timeZone": "UTC"
                    }
                }]
            },
            "meetingDuration": f"PT{duration_minutes}M",
            "returnSuggestionReasons": True,
            "minimumAttendeePercentage": 100
        }
        
        response = session.post(url, json=meeting_body)
        if not response.ok:
            handle_graph_error(response)
            
        return response.json()
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while finding meeting times: {str(e)}")

def create_recurring_event(current_user: str, title: str, start_time: str,
                         end_time: str, description: str, recurrence_pattern: Dict, access_token: str = None) -> Dict:
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
    
    Returns:
        Created recurring event details
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        
        event_body = {
            "subject": title,
            "body": {
                "contentType": "text",
                "content": description
            },
            "start": {
                "dateTime": start_time,
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_time,
                "timeZone": "UTC"
            },
            "recurrence": recurrence_pattern
        }
        
        url = f"{GRAPH_ENDPOINT}/me/events"
        response = session.post(url, json=event_body)
        
        if not response.ok:
            handle_graph_error(response)
            
        return format_event(response.json())
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while creating recurring event: {str(e)}")

def update_recurring_event(current_user: str, event_id: str, updated_fields: Dict,
                         update_type: str = 'series', access_token: str = None) -> Dict:
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
        if update_type == 'series':
            url += "/series"
            
        response = session.patch(url, json=updated_fields)
        
        if not response.ok:
            handle_graph_error(response)
            
        return format_event(response.json())
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while updating recurring event: {str(e)}")

def add_attachment(current_user: str, event_id: str, file_name: str, 
                  content_bytes: bytes, content_type: str, access_token: str = None) -> Dict:
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
            "contentBytes": base64.b64encode(content_bytes).decode('utf-8'),
            "contentType": content_type
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
            
        return response.json().get('value', [])
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching attachments: {str(e)}")

def delete_attachment(current_user: str, event_id: str, attachment_id: str, access_token: str) -> Dict:
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
            return {'status': 'deleted', 'id': attachment_id}
            
        handle_graph_error(response)
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while deleting attachment: {str(e)}")

def get_calendar_permissions(current_user: str, calendar_id: str, access_token: str) -> List[Dict]:
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
            
        return response.json().get('value', [])
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching calendar permissions: {str(e)}")

def share_calendar(current_user: str, calendar_id: str, user_email: str, 
                  role: str = 'read', access_token: str = None) -> Dict:
    """
    Share a calendar with another user.
    
    Args:
        current_user: The user's identifier
        calendar_id: Calendar ID
        user_email: Email of the user to share with
        role: Permission level ('read', 'write', 'owner')
    
    Returns:
        Permission details
    """
    role_map = {
        'read': 'reader',
        'write': 'writer',
        'owner': 'owner'
    }
    
    if role not in role_map:
        raise ValueError(f"Invalid role. Must be one of: {list(role_map.keys())}")
    
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/calendarPermissions"
        
        permission_body = {
            "emailAddress": {
                "address": user_email
            },
            "role": role_map[role]
        }
        
        response = session.post(url, json=permission_body)
        
        if not response.ok:
            handle_graph_error(response)
            
        return response.json()
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while sharing calendar: {str(e)}")

def remove_calendar_sharing(current_user: str, calendar_id: str, 
                          permission_id: str, access_token: str) -> Dict:
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
            return {'status': 'deleted', 'id': permission_id}
            
        handle_graph_error(response)
        
    except requests.RequestException as e:
        raise CalendarError(f"Network error while removing calendar sharing: {str(e)}")

def list_calendar_events(current_user: str, calendar_id: str, access_token: str) -> List[Dict]:
    """
    List events for a given calendar.
    
    Args:
        current_user: User identifier
        calendar_id: ID of the calendar to list events from
        access_token: Optional access token
    
    Returns:
        List of formatted event details from the specified calendar
    
    Raises:
        CalendarError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/calendars/{calendar_id}/events"
        all_events = []
        while url:
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)
            data = response.json()
            events = data.get('value', [])
            all_events.extend([format_event(evt) for evt in events])
            url = data.get('@odata.nextLink')  # Continue if there's pagination
        return all_events
    except requests.RequestException as e:
        raise CalendarError(f"Network error while fetching calendar events: {str(e)}")