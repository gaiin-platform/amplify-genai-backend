from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from io import BytesIO
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_drive"


def get_drive_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("drive", "v3", credentials=credentials)


def convert_dictionaries(input_list):
    result = []
    for item in input_list:
        name = item["name"]

        if item["mimeType"] == "application/vnd.google-apps.folder":
            name = "/" + name
        file_data = [item["id"], name, item["mimeType"]]
        if item.get("size"):
            file_data.append(item["size"])
        if item.get("webContentLink"):
            file_data.append(item["webContentLink"])
        if item.get("parents"):
            file_data.append(item["parents"])
        result.append(file_data)
    return result


def get_file_metadata(current_user, file_id, access_token=None):
    service = get_drive_service(current_user, access_token)
    metadata = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,createdTime,modifiedTime,size")
        .execute()
    )

    result = [
        metadata["id"],
        (
            "/" + metadata["name"]
            if metadata["mimeType"] == "application/vnd.google-apps.folder"
            else metadata["name"]
        ),
        f"mimeType={metadata['mimeType']}",
        f"createdTime={metadata['createdTime']}",
        f"modifiedTime={metadata['modifiedTime']}",
        f"size={metadata.get('size', 'N/A')}",
    ]

    return result


def get_file_content(current_user, file_id, access_token=None):
    service = get_drive_service(current_user, access_token)
    request = service.files().get_media(fileId=file_id)
    file = BytesIO()
    downloader = MediaIoBaseDownload(file, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    return file.getvalue().decode("utf-8")


def create_file(
    current_user, file_name, content, mime_type="text/plain", access_token=None
):
    service = get_drive_service(current_user, access_token)
    file_metadata = {"name": file_name}
    media = MediaIoBaseUpload(BytesIO(content.encode()), mimetype=mime_type)
    file = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id")
        .execute()
    )
    return file.get("id")


def get_download_link(
    current_user, file_id, access_token=None, service=None, allow_conversion=True
):
    # prevent duplicate service creation
    if not service:
        service = get_drive_service(current_user, access_token)
    file = (
        service.files()
        .get(fileId=file_id, fields="id,webContentLink,name,mimeType")
        .execute()
    )
    if file.get("webContentLink"):
        return {
            "id": file.get("id"),
            "downloadLink": file.get("webContentLink"),
            "name": file.get("name"),
            "mimeType": file.get("mimeType"),
        }
    else:
        if (
            allow_conversion
        ):  # prevent infinite loop for download links => convert file => download link => convert file ...
            mime_type = file.get("mimeType", "")
            if (
                mime_type.startswith("application/vnd.google-apps")
                and mime_type != "application/vnd.google-apps.folder"
            ):
                print(f"No webContentLink found for file {file_id}, converting file...")
                # Map of Google Workspace types to export formats
                export_formats = {
                    "application/vnd.google-apps.document": "application/pdf",  # Docs to PDF
                    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # Sheets to XLSX
                    "application/vnd.google-apps.presentation": "application/pdf",  # Slides to PDF
                    "application/vnd.google-apps.drawing": "application/pdf",  # Drawings to PDF
                    "application/vnd.google-apps.jam": "application/pdf",  # Jamboard to PDF
                    "application/vnd.google-apps.form": "application/pdf",  # Forms to PDF
                    "application/vnd.google-apps.script": "application/json",  # Scripts to JSON
                    "application/vnd.google-apps.site": "text/plain",  # Sites to text (limited support)
                    "application/vnd.google-apps.fusiontable": "text/csv",  # Fusion Tables to CSV
                    "application/vnd.google-apps.map": "application/pdf",  # My Maps to PDF
                    "application/vnd.google-apps.drive-sdk": "application/pdf",  # Drive SDK to PDF
                    # Special handling for Colab notebooks
                    "application/vnd.google.colaboratory": "application/json",  # Colab to JSON
                }
                target_format = export_formats.get(mime_type, "application/pdf")
                # Convert the file
                try:
                    converted_file = convert_file(
                        current_user, file_id, target_format, access_token, service
                    )
                    if converted_file and converted_file.get("downloadLink"):
                        return converted_file
                except Exception as e:
                    print(f"Error converting file: {e}")
        else:
            print(
                f"No webContentLink found for file {file_id}, no conversion attempted"
            )
    return None


def create_shared_link(current_user, file_id, permission="view", access_token=None):
    service = get_drive_service(current_user, access_token)
    if permission not in ["view", "edit"]:
        raise ValueError("Permission must be 'view' or 'edit'")

    file = service.files().get(fileId=file_id, fields="webViewLink").execute()
    link = file.get("webViewLink")

    permission_body = {
        "type": "anyone",
        "role": "reader" if permission == "view" else "writer",
    }
    service.permissions().create(fileId=file_id, body=permission_body).execute()

    return link


def share_file(current_user, file_id, emails, role="reader", access_token=None):
    service = get_drive_service(current_user, access_token)
    if role not in ["reader", "commenter", "writer"]:
        raise ValueError("Role must be 'reader', 'commenter', or 'writer'")

    batch = service.new_batch_http_request()
    for email in emails:
        permission = {"type": "user", "role": role, "emailAddress": email}
        batch.add(
            service.permissions().create(
                fileId=file_id, body=permission, sendNotificationEmail=False
            )
        )

    batch.execute()
    return f"File shared with {len(emails)} email(s)"


def convert_file(
    current_user, file_id, target_mime_type, access_token=None, service=None
):
    # prevent duplicate service creation
    if not service:
        service = get_drive_service(current_user, access_token)

    # Get the current file's metadata
    file = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    mime_type = file.get("mimeType", "")

    # Handle Google Workspace files using export
    if (
        mime_type.startswith("application/vnd.google-apps")
        and mime_type != "application/vnd.google-apps.folder"
    ):
        # For Google Workspace files, use the export method
        try:
            # Create a BytesIO object to store the exported file
            file_content = BytesIO()
            request = service.files().export_media(
                fileId=file_id, mimeType=target_mime_type
            )
            downloader = MediaIoBaseDownload(file_content, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            # Create a new file with the exported content
            new_filename = f"{file['name']}"
            file_content.seek(0)
            new_file_metadata = {"name": new_filename}
            media = MediaIoBaseUpload(file_content, mimetype=target_mime_type)
            converted_file = (
                service.files()
                .create(
                    body=new_file_metadata,
                    media_body=media,
                    fields="id,name,mimeType,webContentLink",
                )
                .execute()
            )

            return {
                "id": converted_file.get("id"),
                "name": converted_file.get("name"),
                "mimeType": converted_file.get("mimeType"),
                "downloadLink": converted_file.get("webContentLink"),
            }
        except Exception as e:
            print(f"Error exporting file: {e}")
            return None
    else:
        # For non-Google Workspace files, use the copy method (though this likely won't change the format)
        body = {"name": file["name"], "mimeType": target_mime_type}
        converted_file = (
            service.files()
            .copy(fileId=file_id, body=body, fields="id,name,mimeType,webContentLink")
            .execute()
        )

        return {
            "id": converted_file.get("id"),
            "name": converted_file.get("name"),
            "mimeType": converted_file.get("mimeType"),
            "downloadLink": converted_file.get("webContentLink"),
        }


def move_item(current_user, item_id, destination_folder_id, access_token=None):
    service = get_drive_service(current_user, access_token)
    file = service.files().get(fileId=item_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    file = (
        service.files()
        .update(
            fileId=item_id,
            addParents=destination_folder_id,
            removeParents=previous_parents,
            fields="id, parents",
        )
        .execute()
    )
    return file


def copy_item(current_user, item_id, new_name=None, access_token=None):
    service = get_drive_service(current_user, access_token)
    body = {"name": new_name} if new_name else {}
    copied_file = service.files().copy(fileId=item_id, body=body).execute()
    return convert_dictionaries([copied_file])[0]


def rename_item(current_user, item_id, new_name, access_token=None):
    service = get_drive_service(current_user, access_token)
    file = service.files().update(fileId=item_id, body={"name": new_name}).execute()
    return convert_dictionaries([file])[0]


def get_file_revisions(current_user, file_id, access_token=None):
    service = get_drive_service(current_user, access_token)
    revisions = service.revisions().list(fileId=file_id).execute()
    return revisions.get("revisions", [])


def create_folder(current_user, folder_name, parent_id=None, access_token=None):
    service = get_drive_service(current_user, access_token)
    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        file_metadata["parents"] = [parent_id]
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder


def delete_item_permanently(current_user, item_id, access_token=None):
    service = get_drive_service(current_user, access_token)
    service.files().delete(fileId=item_id).execute()
    return {
        "success": True,
        "message": f"Item with ID {item_id} has been permanently deleted.",
    }


def list_files(current_user, folder_id=None, access_token=None):
    if folder_id is None:
        root_folder_ids = get_root_folder_ids(current_user, access_token)
        return convert_dictionaries(root_folder_ids)

    service = get_drive_service(current_user, access_token)
    query = f"'{folder_id}' in parents" if folder_id else None

    all_files = []
    page_token = None

    while True:
        # Get a batch of files with pagination
        results = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size, webContentLink, parents)",
                pageToken=page_token,
            )
            .execute()
        )

        # Add the current batch to our collection
        all_files.extend(results.get("files", []))

        # Check if there are more pages
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return convert_dictionaries(all_files)


def search_files(current_user, query, access_token=None):
    service = get_drive_service(current_user, access_token)
    formatted_query = f"name contains '{query}'"
    results = (
        service.files()
        .list(q=formatted_query, fields="files(id, name, mimeType)")
        .execute()
    )
    return convert_dictionaries(results.get("files", []))


def list_folders(current_user, parent_folder_id=None, access_token=None):
    if parent_folder_id is None:
        root_folder_ids = get_root_folder_ids(current_user, access_token)
        return convert_dictionaries(root_folder_ids)

    service = get_drive_service(current_user, access_token)
    query = "mimeType='application/vnd.google-apps.folder'"
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"
    results = (
        service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    )
    return convert_dictionaries(results.get("files", []))


def get_root_folder_ids(current_user, access_token=None):
    service = get_drive_service(current_user, access_token)
    results = (
        service.files()
        .list(
            q="'root' in parents and mimeType='application/vnd.google-apps.folder'",
            fields="files(id, name, mimeType)",
        )
        .execute()
    )
    root_folders = results.get("files", [])
    return convert_dictionaries(root_folders)
