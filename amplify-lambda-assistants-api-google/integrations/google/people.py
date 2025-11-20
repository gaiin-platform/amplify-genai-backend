from googleapiclient.discovery import build
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_contacts"


def get_people_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("people", "v1", credentials=credentials)


def search_contacts(current_user, query, page_size=10, access_token=None):
    service = get_people_service(current_user, access_token)
    results = (
        service.people()
        .searchContacts(
            query=query,
            pageSize=page_size,
            readMask="names,emailAddresses,phoneNumbers",
        )
        .execute()
    )
    return results.get("results", [])


def get_contact_details(current_user, resource_name, access_token=None):
    service = get_people_service(current_user, access_token)
    return (
        service.people()
        .get(
            resourceName=resource_name, personFields="names,emailAddresses,phoneNumbers"
        )
        .execute()
    )


def create_contact(current_user, contact_info, access_token=None):
    service = get_people_service(current_user, access_token)
    return service.people().createContact(body=contact_info).execute()


def update_contact(current_user, resource_name, contact_info, access_token=None):
    service = get_people_service(current_user, access_token)
    return (
        service.people()
        .updateContact(
            resourceName=resource_name,
            body=contact_info,
            updatePersonFields="names,emailAddresses,phoneNumbers",
        )
        .execute()
    )


def delete_contact(current_user, resource_name, access_token=None):
    service = get_people_service(current_user, access_token)
    service.people().deleteContact(resourceName=resource_name).execute()


def list_contact_groups(current_user, access_token=None):
    service = get_people_service(current_user, access_token)
    results = service.contactGroups().list().execute()
    return results.get("contactGroups", [])


def create_contact_group(current_user, group_name, access_token=None):
    service = get_people_service(current_user, access_token)
    return (
        service.contactGroups()
        .create(body={"contactGroup": {"name": group_name}})
        .execute()
    )


def update_contact_group(current_user, resource_name, new_name, access_token=None):
    service = get_people_service(current_user, access_token)
    return (
        service.contactGroups()
        .update(resourceName=resource_name, body={"contactGroup": {"name": new_name}})
        .execute()
    )


def delete_contact_group(current_user, resource_name, access_token=None):
    service = get_people_service(current_user, access_token)
    service.contactGroups().delete(resourceName=resource_name).execute()


def add_contacts_to_group(
    current_user, group_resource_name, contact_resource_names, access_token=None
):
    service = get_people_service(current_user, access_token)
    return (
        service.contactGroups()
        .members()
        .modify(
            resourceName=group_resource_name,
            body={"resourceNamesToAdd": contact_resource_names},
        )
        .execute()
    )


def remove_contacts_from_group(
    current_user, group_resource_name, contact_resource_names, access_token=None
):
    service = get_people_service(current_user, access_token)
    return (
        service.contactGroups()
        .members()
        .modify(
            resourceName=group_resource_name,
            body={"resourceNamesToRemove": contact_resource_names},
        )
        .execute()
    )
