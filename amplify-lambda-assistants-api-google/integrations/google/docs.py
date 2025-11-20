from googleapiclient.discovery import build
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_docs"


def create_new_document(current_user, title, access_token=None):
    service = get_docs_service(current_user, access_token)
    body = {"title": title}
    return service.documents().create(body=body).execute()


def get_document_contents(current_user, document_id, access_token=None):
    service = get_docs_service(current_user, access_token)
    return service.documents().get(documentId=document_id).execute()


def insert_text(current_user, document_id, text, index, access_token=None):
    service = get_docs_service(current_user, access_token)
    requests = [{"insertText": {"location": {"index": index}, "text": text}}]
    body = {"requests": requests}
    return service.documents().batchUpdate(documentId=document_id, body=body).execute()


def append_text(current_user, document_id, text, access_token=None):
    service = get_docs_service(current_user, access_token)

    # Get the current document content
    document = service.documents().get(documentId=document_id).execute()

    # Find the end index of the document
    end_index = document["body"]["content"][-1]["endIndex"] - 1

    # Prepare the request to insert text at the end
    requests = [{"insertText": {"location": {"index": end_index}, "text": text}}]
    body = {"requests": requests}

    return service.documents().batchUpdate(documentId=document_id, body=body).execute()


def find_text_indices(current_user, document_id, search_text, access_token=None):
    service = get_docs_service(current_user, access_token)
    document = service.documents().get(documentId=document_id).execute()
    content = document.get("body").get("content")

    indices = []
    for element in content:
        if "paragraph" in element:
            for run in element.get("paragraph").get("elements"):
                if "textRun" in run:
                    text = run.get("textRun").get("content")
                    start = run.get("startIndex")
                    if search_text in text:
                        text_start = text.index(search_text)
                        indices.append(
                            {
                                "start": start + text_start,
                                "end": start + text_start + len(search_text),
                            }
                        )

    return indices


def replace_text(current_user, document_id, old_text, new_text, access_token=None):
    service = get_docs_service(current_user, access_token)
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": old_text},
                "replaceText": new_text,
            }
        }
    ]
    body = {"requests": requests}
    return service.documents().batchUpdate(documentId=document_id, body=body).execute()


def create_document_outline(
    current_user, document_id, outline_items, access_token=None
):
    service = get_docs_service(current_user, access_token)
    requests = [
        {
            "createParagraphBullets": {
                "range": {"startIndex": item["start"], "endIndex": item["end"]},
                "bulletPreset": "NUMBERED_DECIMAL_NESTED",
            }
        }
        for item in outline_items
    ]
    body = {"requests": requests}
    return service.documents().batchUpdate(documentId=document_id, body=body).execute()


def export_document(current_user, document_id, mime_type, access_token=None):
    service = get_drive_service(current_user, access_token)
    return service.files().export(fileId=document_id, mimeType=mime_type).execute()


def share_document(current_user, document_id, email, role, access_token=None):
    service = get_drive_service(current_user, access_token)
    user_permission = {"type": "user", "role": role, "emailAddress": email}
    return (
        service.permissions()
        .create(fileId=document_id, body=user_permission, fields="id")
        .execute()
    )


def get_docs_service(current_user, access_token=None):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("docs", "v1", credentials=credentials)


def get_drive_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("drive", "v3", credentials=credentials)
