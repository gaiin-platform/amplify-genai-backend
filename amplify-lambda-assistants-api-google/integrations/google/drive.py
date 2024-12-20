from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from io import BytesIO
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_drive"

def get_drive_service(current_user):
    user_credentials = get_user_credentials(current_user, integration_name)
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build('drive', 'v3', credentials=credentials)

def convert_dictionaries(input_list):
    result = []
    for item in input_list:
        name = item['name']
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            name = '/' + name
        result.append([item['id'], name])
    return result

def list_files(current_user, folder_id=None):

    # if folder ID is none, then return the root files themselves
    if folder_id is None:
        root_folder_ids = get_root_folder_ids(current_user)
        return root_folder_ids

    service = get_drive_service(current_user)
    query = f"'{folder_id}' in parents" if folder_id else None
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

def search_files(current_user, query):
    service = get_drive_service(current_user)
    formatted_query = f"name contains '{query}'"
    results = service.files().list(q=formatted_query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

def get_file_metadata(current_user, file_id):
    service = get_drive_service(current_user)
    return service.files().get(fileId=file_id, fields='id,name,mimeType,createdTime,modifiedTime,size').execute()

def get_file_content(current_user, file_id):
    service = get_drive_service(current_user)
    request = service.files().get_media(fileId=file_id)
    file = BytesIO()
    downloader = MediaIoBaseDownload(file, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    return file.getvalue().decode('utf-8')

def create_file(current_user, file_name, content, mime_type='text/plain'):
    service = get_drive_service(current_user)
    file_metadata = {'name': file_name}
    media = MediaIoBaseUpload(BytesIO(content.encode()), mimetype=mime_type)
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id')

def list_folders(current_user, parent_folder_id=None):

    # if parent folder ID is none, return the root folders themselves
    # in the form of a list of dictionaries with keys 'id' and 'name'
    if parent_folder_id is None:
        root_folder_ids = get_root_folder_ids(current_user)
        return root_folder_ids


    service = get_drive_service(current_user)
    query = "mimeType='application/vnd.google-apps.folder'"
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get('files', [])

def get_download_link(current_user, file_id):
    service = get_drive_service(current_user)
    file = service.files().get(fileId=file_id, fields='webContentLink').execute()
    return file.get('webContentLink')

def create_shared_link(current_user, file_id, permission='view'):
    service = get_drive_service(current_user)
    if permission not in ['view', 'edit']:
        raise ValueError("Permission must be 'view' or 'edit'")

    file = service.files().get(fileId=file_id, fields='webViewLink').execute()
    link = file.get('webViewLink')

    permission_body = {
        'type': 'anyone',
        'role': 'reader' if permission == 'view' else 'writer'
    }
    service.permissions().create(fileId=file_id, body=permission_body).execute()

    return link

def share_file(current_user, file_id, emails, role='reader'):
    service = get_drive_service(current_user)
    if role not in ['reader', 'commenter', 'writer']:
        raise ValueError("Role must be 'reader', 'commenter', or 'writer'")

    batch = service.new_batch_http_request()
    for email in emails:
        permission = {
            'type': 'user',
            'role': role,
            'emailAddress': email
        }
        batch.add(service.permissions().create(fileId=file_id, body=permission, sendNotificationEmail=False))

    batch.execute()
    return f"File shared with {len(emails)} email(s)"

def convert_file(current_user, file_id, target_mime_type):
    service = get_drive_service(current_user)

    # Get the current file's metadata
    file = service.files().get(fileId=file_id, fields='name').execute()

    # Create a copy of the file with the new MIME type
    body = {
        'name': f"{file['name']} (Converted)",
        'mimeType': target_mime_type
    }
    converted_file = service.files().copy(fileId=file_id, body=body).execute()

    # Get the download link for the converted file
    download_link = get_download_link(current_user, converted_file['id'])

    return {
        'id': converted_file['id'],
        'name': converted_file['name'],
        'mimeType': converted_file['mimeType'],
        'downloadLink': download_link
    }

def move_item(current_user, item_id, destination_folder_id):
    service = get_drive_service(current_user)
    file = service.files().get(fileId=item_id, fields='parents').execute()
    previous_parents = ",".join(file.get('parents', []))
    file = service.files().update(
        fileId=item_id,
        addParents=destination_folder_id,
        removeParents=previous_parents,
        fields='id, parents'
    ).execute()
    return file

def copy_item(current_user, item_id, new_name=None):
    service = get_drive_service(current_user)
    body = {'name': new_name} if new_name else {}
    copied_file = service.files().copy(fileId=item_id, body=body).execute()
    return copied_file

def rename_item(current_user, item_id, new_name):
    service = get_drive_service(current_user)
    file = service.files().update(fileId=item_id, body={'name': new_name}).execute()
    return file

# 2. Rename files or folders (already provided in previous response)

# 3. Get file revisions
def get_file_revisions(current_user, file_id):
    service = get_drive_service(current_user)
    revisions = service.revisions().list(fileId=file_id).execute()
    return revisions.get('revisions', [])


# 4. Create a new folder
def create_folder(current_user, folder_name, parent_id=None):
    service = get_drive_service(current_user)
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]
    folder = service.files().create(body=file_metadata, fields='id').execute()
    return folder


def delete_item_permanently(current_user, item_id):
    service = get_drive_service(current_user)
    service.files().delete(fileId=item_id).execute()
    return {"success": True, "message": f"Item with ID {item_id} has been permanently deleted."}


def get_root_folder_ids(current_user):
    service = get_drive_service(current_user)
    results = service.files().list(q="'root' in parents and mimeType='application/vnd.google-apps.folder'",
                                   fields="files(id, name)").execute()
    root_folders = results.get('files', [])
    return [{'id': folder['id'], 'name': folder['name']} for folder in root_folders]







