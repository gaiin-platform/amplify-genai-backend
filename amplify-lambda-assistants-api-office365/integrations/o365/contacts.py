import json
import requests
from typing import Dict, List, Optional
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_contacts"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


class ContactError(Exception):
    """Base exception for contact operations"""

    pass


class ContactNotFoundError(ContactError):
    """Raised when a contact cannot be found"""

    pass


def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        raise ContactNotFoundError("Contact not found")
    try:
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "Unknown error")
    except json.JSONDecodeError:
        error_message = response.text
    raise ContactError(
        f"Graph API error: {error_message} (Status: {response.status_code})"
    )


def list_contacts(
    current_user: str, page_size: int = 10, access_token: str = None
) -> List[Dict]:
    """
    Retrieve a list of contacts with pagination support.

    Args:
        current_user: User identifier
        page_size: Number of contacts to retrieve per page

    Returns:
        List of contact dictionaries

    Raises:
        ContactError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/contacts?$top={page_size}"

        all_contacts = []
        while url:
            response = session.get(url)
            if not response.ok:
                handle_graph_error(response)

            data = response.json()
            contacts = data.get("value", [])
            all_contacts.extend([format_contact(contact) for contact in contacts])

            # Handle pagination
            url = data.get("@odata.nextLink")

        return all_contacts

    except requests.RequestException as e:
        raise ContactError(f"Network error while fetching contacts: {str(e)}")


def get_contact_details(current_user: str, contact_id: str, access_token: str) -> Dict:
    """
    Get details for a specific contact.

    Args:
        current_user: User identifier
        contact_id: Contact ID to retrieve

    Returns:
        Dict containing contact details

    Raises:
        ContactNotFoundError: If contact doesn't exist
        ContactError: For other retrieval failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/contacts/{contact_id}"
        response = session.get(url)

        if not response.ok:
            handle_graph_error(response)

        contact = response.json()
        return format_contact(contact)

    except requests.RequestException as e:
        raise ContactError(f"Network error while fetching contact: {str(e)}")


def create_contact(
    current_user: str,
    given_name: str,
    surname: str,
    email_addresses: List[str],
    access_token: str,
) -> Dict:
    """
    Create a new contact.

    Args:
        current_user: User identifier
        given_name: Contact's first name
        surname: Contact's last name
        email_addresses: List of email addresses for the contact

    Returns:
        Dict containing created contact details

    Raises:
        ContactError: If contact creation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/contacts"

        # Input validation
        if not given_name and not surname:
            raise ContactError("Either given name or surname must be provided")

        if email_addresses:
            for email in email_addresses:
                if not "@" in email:
                    raise ContactError(f"Invalid email address format: {email}")

        contact_body = {
            "givenName": given_name,
            "surname": surname,
            "emailAddresses": [{"address": addr} for addr in email_addresses],
        }

        response = session.post(url, json=contact_body)

        if not response.ok:
            handle_graph_error(response)

        created_contact = response.json()
        return format_contact(created_contact)

    except requests.RequestException as e:
        raise ContactError(f"Network error while creating contact: {str(e)}")


def delete_contact(current_user: str, contact_id: str, access_token: str) -> Dict:
    """
    Delete a contact.

    Args:
        current_user: User identifier
        contact_id: Contact ID to delete

    Returns:
        Dict containing deletion status

    Raises:
        ContactNotFoundError: If contact doesn't exist
        ContactError: For other deletion failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/contacts/{contact_id}"
        response = session.delete(url)

        if response.status_code == 204:
            return {"status": "deleted", "id": contact_id}

        handle_graph_error(response)

    except requests.RequestException as e:
        raise ContactError(f"Network error while deleting contact: {str(e)}")


def format_contact(contact: Dict) -> Dict:
    """
    Format contact data consistently.

    Args:
        contact: Raw contact data from Graph API

    Returns:
        Dict containing formatted contact details
    """
    return {
        "id": contact["id"],
        "displayName": contact.get("displayName", ""),
        "givenName": contact.get("givenName", ""),
        "surname": contact.get("surname", ""),
        "emailAddresses": [
            email.get("address", "") for email in contact.get("emailAddresses", [])
        ],
        "businessPhones": contact.get("businessPhones", []),
        "mobilePhone": contact.get("mobilePhone", ""),
        "jobTitle": contact.get("jobTitle", ""),
        "companyName": contact.get("companyName", ""),
        "department": contact.get("department", ""),
        "officeLocation": contact.get("officeLocation", ""),
        "createdDateTime": contact.get("createdDateTime", ""),
        "lastModifiedDateTime": contact.get("lastModifiedDateTime", ""),
    }
