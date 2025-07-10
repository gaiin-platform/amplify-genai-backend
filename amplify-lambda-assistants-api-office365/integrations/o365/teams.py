import json
import requests
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_teams"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class TeamsError(Exception):
    """Base exception for Teams operations"""

    pass


class TeamNotFoundError(TeamsError):
    """Raised when a team cannot be found"""

    pass


class ChannelNotFoundError(TeamsError):
    """Raised when a channel cannot be found"""

    pass


class ChatNotFoundError(TeamsError):
    """Raised when a chat cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get("error", {}).get("message", "").lower()
        if "team" in error_message:
            raise TeamNotFoundError("Team not found")
        elif "channel" in error_message:
            raise ChannelNotFoundError("Channel not found")
        elif "chat" in error_message:
            raise ChatNotFoundError("Chat not found")
        raise TeamsError("Resource not found")

    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise TeamsError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_teams(current_user: str, access_token: str = None) -> List[Dict]:
    """
    Lists teams that the user is a member of.

    Args:
        current_user: User identifier

    Returns:
        List of team details

    Raises:
        TeamsError: If operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/joinedTeams"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        teams = response.json().get("value", [])
        return [format_team(team) for team in teams]

    except requests.RequestException as e:
        raise TeamsError(f"Network error while listing teams: {str(e)}")


def list_channels(
    current_user: str, team_id: str, access_token: str = None
) -> List[Dict]:
    """
    Lists channels in a team.

    Args:
        current_user: User identifier
        team_id: Team ID

    Returns:
        List of channel details

    Raises:
        TeamNotFoundError: If team doesn't exist
        TeamsError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/teams/{team_id}/channels"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        channels = response.json().get("value", [])
        return [format_channel(channel) for channel in channels]

    except requests.RequestException as e:
        raise TeamsError(f"Network error while listing channels: {str(e)}")


def create_channel(
    current_user: str,
    team_id: str,
    name: str,
    description: str = "",
    access_token: str = None,
) -> Dict:
    """
    Creates a new channel in a team.

    Args:
        current_user: User identifier
        team_id: Team ID
        name: Channel name
        description: Channel description (optional)

    Returns:
        Dict containing created channel details

    Raises:
        TeamNotFoundError: If team doesn't exist
        TeamsError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/teams/{team_id}/channels"

        # Validate channel name
        if not name or len(name) > 50:
            raise TeamsError("Channel name must be between 1 and 50 characters")

        body = {
            "displayName": name,
            "description": description,
            "membershipType": "standard",
        }

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_channel(response.json())

    except requests.RequestException as e:
        raise TeamsError(f"Network error while creating channel: {str(e)}")


def send_channel_message(
    current_user: str,
    team_id: str,
    channel_id: str,
    message: str,
    importance: str = "normal",
    access_token: str = None,
) -> Dict:
    """
    Sends a message to a channel.

    Args:
        current_user: User identifier
        team_id: Team ID
        channel_id: Channel ID
        message: Message content (can include basic HTML)
        importance: Message importance ('normal', 'high', 'urgent')

    Returns:
        Dict containing sent message details

    Raises:
        TeamNotFoundError: If team doesn't exist
        ChannelNotFoundError: If channel doesn't exist
        TeamsError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/teams/{team_id}/channels/{channel_id}/messages"

        if importance not in ["normal", "high", "urgent"]:
            raise TeamsError("Invalid importance level")

        body = {
            "body": {"content": message, "contentType": "html"},
            "importance": importance,
        }

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_message(response.json())

    except requests.RequestException as e:
        raise TeamsError(f"Network error while sending message: {str(e)}")


def get_chat_messages(
    current_user: str, chat_id: str, top: int = 50, access_token: str = None
) -> List[Dict]:
    """
    Gets messages from a chat.

    Args:
        current_user: User identifier
        chat_id: Chat ID
        top: Maximum number of messages to retrieve

    Returns:
        List of message details

    Raises:
        ChatNotFoundError: If chat doesn't exist
        TeamsError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/chats/{chat_id}/messages?$top={top}"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        messages = response.json().get("value", [])
        return [format_message(msg) for msg in messages]

    except requests.RequestException as e:
        raise TeamsError(f"Network error while getting messages: {str(e)}")


def schedule_meeting(
    current_user: str,
    team_id: str,
    subject: str,
    start_time: str,
    end_time: str,
    attendees: List[str] = None,
    access_token: str = None,
) -> Dict:
    """
    Schedules a Teams meeting.

    Args:
        current_user: User identifier
        team_id: Team ID
        subject: Meeting subject
        start_time: Start time in ISO format
        end_time: End time in ISO format
        attendees: List of attendee email addresses

    Returns:
        Dict containing scheduled meeting details

    Raises:
        TeamNotFoundError: If team doesn't exist
        TeamsError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/teams/{team_id}/schedule"

        # Validate datetime formats
        try:
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise TeamsError("Invalid datetime format")

        body = {
            "subject": subject,
            "startDateTime": start_time,
            "endDateTime": end_time,
            "attendees": [
                {"emailAddress": {"address": email}} for email in (attendees or [])
            ],
        }

        response = session.post(url, json=body)

        if not response.ok:
            handle_graph_error(response)

        return format_meeting(response.json())

    except requests.RequestException as e:
        raise TeamsError(f"Network error while scheduling meeting: {str(e)}")


def format_team(team: Dict) -> Dict:
    """Format team data consistently"""
    return {
        "id": team["id"],
        "displayName": team.get("displayName", ""),
        "description": team.get("description", ""),
        "visibility": team.get("visibility", "private"),
        "isArchived": team.get("isArchived", False),
        "webUrl": team.get("webUrl", ""),
    }


def format_channel(channel: Dict) -> Dict:
    """Format channel data consistently"""
    return {
        "id": channel["id"],
        "displayName": channel.get("displayName", ""),
        "description": channel.get("description", ""),
        "membershipType": channel.get("membershipType", "standard"),
        "email": channel.get("email", ""),
        "webUrl": channel.get("webUrl", ""),
    }


def format_message(message: Dict) -> Dict:
    """Format message data consistently"""
    return {
        "id": message["id"],
        "content": message.get("body", {}).get("content", ""),
        "contentType": message.get("body", {}).get("contentType", "text"),
        "createdDateTime": message.get("createdDateTime", ""),
        "importance": message.get("importance", "normal"),
        "from": message.get("from", {}).get("user", {}).get("displayName", ""),
        "reactions": message.get("reactions", []),
        "attachments": message.get("attachments", []),
    }


def format_meeting(meeting: Dict) -> Dict:
    """Format meeting data consistently"""
    return {
        "id": meeting["id"],
        "subject": meeting.get("subject", ""),
        "startDateTime": meeting.get("startDateTime", ""),
        "endDateTime": meeting.get("endDateTime", ""),
        "joinUrl": meeting.get("joinUrl", ""),
        "attendees": [
            {
                "name": attendee.get("emailAddress", {}).get("name", ""),
                "email": attendee.get("emailAddress", {}).get("address", ""),
            }
            for attendee in meeting.get("attendees", [])
        ],
    }
