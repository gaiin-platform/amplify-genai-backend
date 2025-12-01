import json
import os
import re
import boto3
import requests
from datetime import datetime
import copy
from integrations.oauth import IntegrationType, provider_case
from integrations.oauth import get_user_credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from io import BytesIO
from google.oauth2.credentials import Credentials
import asyncio
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any

from pycommon.authz import validated, setup_validated
from pycommon.api.files import upload_file, delete_file
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)


API_URL = os.environ["API_BASE_URL"]


# unifies location for functions needed in datasource file manager component.
@validated("list_files")
def list_integration_files(event, context, current_user, name, data):
    token = data["access_token"]
    data = data["data"]
    integration = data["integration"]
    integration_provider = provider_case(integration)
    folder_id = data.get("folder_id")

    print(f"Listing files for integration: {integration_provider}")
    result = list_files(integration_provider, token, folder_id, integration)
    if result:
        return {"success": True, "data": result}

    return {"success": False, "error": "No integration files found"}


def list_files(integration_provider, token, folder_id=None, integration=None):
    """
    Creates an OAuth client for either Google or Microsoft integrations.
    Returns a tuple of (client, is_google_flow) where is_google_flow is used to determine
    how to handle the client in other functions.
    """
    match integration_provider:
        case IntegrationType.GOOGLE:
            result = execute_request(
                token,
                "/google/integrations/list_files",
                {"folderId": folder_id if folder_id else ""},
            )
            if result:
                print(f"Google list_files result: {result}")
                
                # If no folder_id specified, filter to show only root-level items
                if not folder_id:
                    print("Filtering to root-level items only")
                    result = filter_to_root_level_items(result)
                
                files = []
                for file_list in result:
                    files.append(
                        {
                            "id": file_list[0],
                            "name": file_list[1],
                            "mimeType": file_list[2],
                            "size": file_list[3] if len(file_list) > 3 else "N/A",
                            "downloadLink": (
                                file_list[4] if len(file_list) > 4 else None
                            ),
                        }
                    )
                
                return files

        case IntegrationType.MICROSOFT:
            # Handle both microsoft_drive and microsoft_sharepoint
            if integration == "microsoft_drive":
                return execute_request(
                    token,
                    "/microsoft/integrations/list_drive_items",
                    {"folder_id": folder_id if folder_id else "root", "page_size": 100},
                )
            elif integration == "microsoft_sharepoint":
                level, site_id, drive_id, folder_path = parse_sharepoint_folder_id(folder_id)
                
                if level == "sites":
                    # Root level - list SharePoint sites as "folders"
                    sites = execute_request(token, "/microsoft/integrations/list_sites", {"top": 50})
                    if sites:
                        return format_sites_as_folders(sites)
                    return None
                    
                elif level == "libraries":
                    # Site level - list document libraries as "folders"
                    libraries = execute_request(token, "/microsoft/integrations/list_document_libraries", 
                                              {"site_id": site_id, "top": 50})
                    if libraries:
                        return format_libraries_as_folders(libraries, site_id)
                    return None
                    
                elif level == "files":
                    # Library level - list actual files and folders
                    files = execute_request(
                        token,
                        "/microsoft/integrations/list_library_files",
                        {"site_id": site_id, "drive_id": drive_id, "folder_path": folder_path, "top": 100},
                    )
                    if files:
                        return format_sharepoint_files_with_folder_context(files, site_id, drive_id, folder_path)
                    return None
                    
                return None
            else:
                # Default to drive for backwards compatibility
                return execute_request(
                    token,
                    "/microsoft/integrations/list_drive_items",
                    {"folder_id": folder_id if folder_id else "root", "page_size": 100},
                )

    print(f"No result from list_files for integration: {integration_provider}")
    return None


@validated("download_file")
def download_integration_file(event, context, current_user, name, data):
    token = data["access_token"]
    data = data["data"]
    integration = data["integration"]
    integration_provider = provider_case(integration)
    file_id = data.get("file_id")
    direct_download = data.get("direct_download", True)
    return prepare_download_link(integration, integration_provider, file_id, current_user, token, direct_download)

def prepare_download_link(integration, integration_provider, file_id, current_user, token, direct_download=False):
    print(f"Starting download for integration {integration_provider}, file {file_id}")
    result = request_download_link(integration_provider, file_id, token, integration)
    print(f"Download link result: {result}")

    if result and "downloadLink" in result:
        try:
            download_url = result["downloadLink"]

            download_file_id = result.get("id")
            requires_cleanup = file_id != download_file_id

            # when downloaded directly to the user
            if direct_download and not requires_cleanup:
                return {"success": True, "data": download_url}
            else:
                file_name = result.get("name", "downloaded_file")
                file_mime_type = result.get("mimeType", "application/octet-stream")
                file_extension = MIME_TO_EXT.get(file_mime_type, "")
                print(
                    f"File name: {file_name}, mime type: {file_mime_type}, extension: {file_extension}"
                )
                safe_file_name = re.sub(r"[^a-zA-Z0-9._-]", "", file_name)

                if "." in safe_file_name:
                    safe_file_name = safe_file_name.rsplit(".", 1)[0]

                safe_file_name += file_extension

                credentials = get_user_credentials(current_user, integration)
                print(
                    f"Downloading file: {file_name}, mime type: {file_mime_type}, safe name: {safe_file_name}"
                )
                file_content = get_file_contents(
                    integration_provider, credentials, download_file_id, download_url
                )
                if not file_content:
                    return {"success": False, "error": "Failed to get file contents"}

                # Create an S3 key using the safe file name; no double extension.
                key = f"temp_integration_file/{current_user}/{safe_file_name}"

                bucket = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]
                print(f"Saving file to S3 bucket: {bucket}, key: {key}")

                try:
                    s3 = boto3.client("s3")
                    s3.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=file_content,
                        ContentType=file_mime_type,
                    )
                    print("File successfully saved to S3")
                except Exception as s3_error:
                    print(f"S3 upload error details: {s3_error}")
                    raise

                # Set disposition header to use the original filename for download
                response_headers = {
                    "ResponseContentDisposition": f'attachment; filename="{file_name}"',
                    "ResponseContentType": file_mime_type,
                }

                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key, **response_headers},
                    ExpiresIn=3600,  # URL will be valid for 1 hour
                )
                if requires_cleanup:
                    cleanup_after_download_file(
                        integration_provider, download_file_id, token
                    )

                return {"success": True, "data": presigned_url}

        except Exception as e:
            print(f"Error saving file to S3: {e}")
            return {"success": False, "error": f"Error saving file to S3: {str(e)}"}
    else:
        print(f"No download link in result: {result}")
        return {"success": False, "error": "Failed to get download link for file"}


def request_download_link(integration_provider, file_id, token, integration=None):
    """
    Downloads a file from the integration.
    """
    match integration_provider:
        case IntegrationType.GOOGLE:
            return execute_request(
                token, "/google/integrations/get_download_link", {"fileId": file_id}
            )
        case IntegrationType.MICROSOFT:
            # Handle both microsoft_drive and microsoft_sharepoint
            if integration == "microsoft_sharepoint":
                site_id, drive_id, item_id, error = parse_sharepoint_file_id(file_id)
                if error:
                    print(f"SharePoint file_id error: {error}")
                    return None
                return execute_request(
                    token, "/microsoft/integrations/get_sharepoint_file_download_url", 
                    {"site_id": site_id, "drive_id": drive_id, "item_id": item_id}
                )
            else:
                # Default to drive (microsoft_drive or backwards compatibility)
                return execute_request(
                    token, "/microsoft/integrations/download_file", {"item_id": file_id}
                )


def get_file_contents(integration_provider, credentials, file_id, download_url):
    print(
        f"Getting file contents for integration: {integration_provider}, file_id: {file_id}"
    )
    try:
        match integration_provider:
            case IntegrationType.GOOGLE:
                credentials = Credentials.from_authorized_user_info(credentials)
                service = build("drive", "v3", credentials=credentials)
                request = service.files().get_media(fileId=file_id)
                file = BytesIO()
                downloader = MediaIoBaseDownload(file, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                return file.getvalue()
            case IntegrationType.MICROSOFT:
                integration_token = credentials["token"]
                headers = {"Authorization": f"Bearer {integration_token}"}
                response = requests.get(download_url, headers=headers, timeout=30)
                if not response.ok:
                    print(
                        f"Error downloading Microsoft file: HTTP {response.status_code} - {response.reason}"
                    )
                    return None

                # Use BytesIO to accumulate streamed content
                file_content = BytesIO()
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_content.write(chunk)

                return file_content.getvalue()
    except Exception as e:
        print(
            f"Error getting file contents for integration: {integration_provider} - error: {e}"
        )
        return None


def parse_sharepoint_folder_id(folder_id):
    """Parse SharePoint folder_id for multi-level navigation.
    
    Navigation levels:
    - Empty/"root": List sites
    - "site_id": List document libraries in site
    - "site_id:drive_id": List files in library root
    - "site_id:drive_id:folder_path": List files in specific folder
    """
    if not folder_id or folder_id == "root":
        return "sites", None, None, None
    
    parts = folder_id.split(":", 2)
    if len(parts) == 1:
        # Just site_id -> list document libraries
        return "libraries", parts[0], None, None
    elif len(parts) == 2:
        # site_id:drive_id -> list files in library root
        return "files", parts[0], parts[1], "root"
    elif len(parts) >= 3:
        # site_id:drive_id:folder_path -> list files in folder
        return "files", parts[0], parts[1], parts[2]
    
    return None, None, None, None


def parse_sharepoint_file_id(file_id):
    """Parse SharePoint file_id in format 'site_id:drive_id:item_id'."""
    if not file_id:
        return None, None, None, "SharePoint file_id cannot be empty"
    
    parts = file_id.split(":", 2)
    if len(parts) >= 3:
        site_id = parts[0]
        drive_id = parts[1]
        item_id = parts[2]
        if not site_id or not drive_id or not item_id:
            return None, None, None, "SharePoint site_id, drive_id, and item_id cannot be empty"
        return site_id, drive_id, item_id, None
    return None, None, None, f"Invalid SharePoint file_id format: '{file_id}'. Expected: 'site_id:drive_id:item_id'"


def format_sites_as_folders(sites):
    """Format SharePoint sites as folders for navigation."""
    formatted = []
    for site in sites:
        formatted.append({
            "id": site["id"],  # This becomes the folder_id for next level
            "name": site.get("displayName", site.get("name", "Unknown Site")),
            "mimeType": "SharePoint Site",  # Mimic folder type
            "size": "N/A",
            "downloadLink": None
        })
    return formatted


def format_libraries_as_folders(libraries, site_id):
    """Format SharePoint document libraries as folders for navigation."""
    formatted = []
    for library in libraries:
        formatted.append({
            "id": f"{site_id}:{library['id']}",  # site_id:drive_id format
            "name": library.get("name", "Unknown Library"),
            "mimeType": "SharePoint Library",  # Mimic folder type
            "size": "N/A", 
            "downloadLink": None
        })
    return formatted


def format_sharepoint_files_with_folder_context(files, site_id, drive_id, current_folder_path="root"):
    """Format SharePoint files to include proper folder_id context for subfolders."""
    if not files:
        return files
        
    formatted = []
    for file in files:
        formatted_file = dict(file)  # Copy original file data
        
        # If it's a folder, update the id to include the full path context
        if file.get("mimeType") and "folder" in file.get("mimeType", "").lower():
            # Construct the full folder path for navigation
            folder_name = file.get("name", "")
            if current_folder_path == "root":
                new_path = folder_name
            else:
                new_path = f"{current_folder_path}/{folder_name}"
            formatted_file["id"] = f"{site_id}:{drive_id}:{new_path}"
        elif file.get("folder"):  # SharePoint API format check
            folder_name = file.get("name", "")
            if current_folder_path == "root":
                new_path = folder_name
            else:
                new_path = f"{current_folder_path}/{folder_name}"
            formatted_file["id"] = f"{site_id}:{drive_id}:{new_path}"
        else:
            # For files, keep the original id but we'll need site_id:drive_id:item_id for downloads
            original_id = file.get("id", "")
            formatted_file["id"] = f"{site_id}:{drive_id}:{original_id}"
            
        formatted.append(formatted_file)
    
    return formatted


def execute_request(access_token, url_path, data):
    print(f"Executing request to {url_path}")
    request = {"data": data}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            f"{API_URL}{url_path}", headers=headers, data=json.dumps(request)
        )

        response_content = (
            response.json()
        )  # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get("success"):
            print(f"Error executing request: {response_content}")
            return None
        elif response.status_code == 200 and response_content.get("success", False):
            print(f"Successfully executed request: ", url_path)
            return response_content.get("data", None)

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return None


def cleanup_after_download_file(integration_provider, download_file_id, token):
    match integration_provider:
        case IntegrationType.GOOGLE:
            # Clean up converted file if it was created during this process
            try:
                print(f"Cleaning up converted file with ID: {download_file_id}")
                cleanup_result = execute_request(
                    token,
                    "/google/integrations/delete_item_permanently",
                    {"itemId": download_file_id},
                )
                if cleanup_result:
                    print(f"Successfully deleted converted file")
                else:
                    print(f"Failed to delete converted file")
            except Exception as e:
                print(f"Error deleting converted file: {e}\n continuing...")
                # Continue even if cleanup fails, don't fail the whole operation
        # case IntegrationType.MICROSOFT:
        # no cleanup required


MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}


@validated("upload_files")
def drive_files_to_data_sources(event, context, current_user, name, data):
    """
    Upload selected files from drive integrations to data sources.
    Syncs files that have been modified since lastCaptured date.
    """
    # Run the async function in a synchronous wrapper
    return asyncio.run(_async_drive_files_to_data_sources(event, context, current_user, name, data))


async def _async_drive_files_to_data_sources(event, context, current_user, name, data):
    """
    Async implementation of drive files to data sources sync.
    """
    try:
        token = data["access_token"]
        payload = data["data"]
        
        print(f"Processing drive files to data sources for user: {current_user}")
        start_time = time.time()
        
        # Create a deep copy of the payload to return with updates
        updated_payload = copy.deepcopy(payload)
        
        # Global cache to track processed files across ALL providers and sections
        processed_files_cache = {}  # file_id -> file_metadata
        
        # Create tasks for each provider to run concurrently
        provider_tasks = []
        provider_names = []
        
        for integration_provider in payload.keys():
            print(f"Creating async task for provider: {integration_provider}")
            task = _process_provider_async(
                integration_provider, 
                payload[integration_provider], 
                token, 
                current_user, 
                processed_files_cache
            )
            provider_tasks.append(task)
            provider_names.append(integration_provider)
        
        # Execute all provider tasks concurrently
        print(f"[ASYNC] Starting concurrent processing of {len(provider_tasks)} providers")
        provider_results = await asyncio.gather(*provider_tasks, return_exceptions=True)
        
        # Process results and handle any exceptions
        for i, (provider_name, result) in enumerate(zip(provider_names, provider_results)):
            if isinstance(result, Exception):
                print(f"Error processing provider {provider_name}: {result}")
                # Keep original data for failed providers
                continue
            else:
                # Update payload with successful results
                updated_payload[provider_name] = result
        
        end_time = time.time()
        
        # Cache statistics
        total_cached = len(processed_files_cache)
        successful_cached = len([f for f in processed_files_cache.values() if f.get("datasource")])
        skipped_cached = len([f for f in processed_files_cache.values() if not f.get("datasource") and f.get("lastCaptured")])
        
        print(f"[ASYNC COMPLETE] Total processing time: {end_time - start_time:.2f}s")
        print(f"[CACHE STATS] Total unique files processed: {total_cached}")
        print(f"[CACHE STATS] Results - Uploaded: {successful_cached} | Skipped (no update needed): {skipped_cached}")
        if total_cached > 0:
            print(f"[CACHE STATS] Cache efficiency: Prevented duplicate processing for any overlapping files")
        
        return {"success": True, "data": updated_payload}
        
    except Exception as e:
        print(f"Error in async drive_files_to_data_sources: {e}")
        return {"success": False, "error": str(e)}


async def _process_provider_async(integration_provider: str, provider_data: Dict, token: str, current_user: str, processed_files_cache: Dict) -> Dict:
    """
    Process a single integration provider asynchronously.
    Returns updated provider data structure.
    """
    try:
        print(f"[ASYNC] Processing provider: {integration_provider}")
        provider_start = time.time()
        
        provider_type = provider_case(integration_provider)
        updated_provider_data = copy.deepcopy(provider_data)
        
        # Process files first (same order as before)
        if "files" in provider_data:
            files_data = provider_data["files"]
            updated_files, cache_updates = process_files_with_cache(
                files_data, provider_type, token, current_user, integration_provider, processed_files_cache
            )
            updated_provider_data["files"] = updated_files
            processed_files_cache.update(cache_updates)
        
        # Process folders (with awareness of already processed files)
        if "folders" in provider_data:
            folders_data = provider_data["folders"]
            updated_folders = process_folders_with_cache(
                folders_data, provider_type, token, current_user, integration_provider, processed_files_cache
            )
            # Filter out empty folders to prevent schema validation errors
            non_empty_folders = {k: v for k, v in updated_folders.items() if v}
            updated_provider_data["folders"] = non_empty_folders
            
            # Remove files from files section if they also appear in folders (prevent duplicates)
            if "files" in updated_provider_data and non_empty_folders:
                files_in_folders = set()
                for folder_files in non_empty_folders.values():
                    files_in_folders.update(folder_files.keys())
                
                # Keep only files that don't appear in any folder
                updated_files = updated_provider_data["files"]
                deduplicated_files = {k: v for k, v in updated_files.items() if k not in files_in_folders}
                updated_provider_data["files"] = deduplicated_files
                
                removed_count = len(updated_files) - len(deduplicated_files)
                if removed_count > 0:
                    print(f"[DEDUP] {integration_provider}: Removed {removed_count} files from files section (appear in folders)")
        
        provider_end = time.time()
        print(f"[ASYNC] {integration_provider} completed in {provider_end - provider_start:.2f}s")
        
        return updated_provider_data
        
    except Exception as e:
        print(f"[ASYNC] Error processing provider {integration_provider}: {e}")
        raise e  # Re-raise to be caught by gather()


def process_files_with_cache(files_data, provider_type, token, current_user, integration_provider, processed_files_cache):
    """Process individual files for syncing with global cache awareness."""
    updated_files = {}
    cache_updates = {}
    total_files = len(files_data)
    processed_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0
    cached_count = 0
    
    print(f"[FILES START] Processing {total_files} files for {integration_provider}")
    
    for file_id in files_data.keys():
        file_metadata = files_data[file_id]
        processed_count += 1
        
        try:
            # Check if file was already processed
            if file_id in processed_files_cache:
                cached_result = processed_files_cache[file_id]
                cache_source = "uploaded" if cached_result.get("datasource") else "skipped"
                print(f"[FILE CACHED] {file_id} - Already {cache_source}, reusing result (saved download + upload)")
                updated_files[file_id] = cached_result
                cache_updates[file_id] = cached_result  # Include cached files in updates
                cached_count += 1
                continue
            
            print(f"[FILE CHECK] {file_id} - {file_metadata.get('lastCaptured', 'Never captured')}")
            
            # Check if file needs to be processed
            needs_update = should_update_file(file_metadata, file_id, provider_type, token, integration_provider)
            
            if needs_update:
                print(f"[FILE {processed_count}/{total_files}] Updating {file_id}")
                
                # Use prepare_download_link to handle file conversion and get presigned URL
                download_result = prepare_download_link(
                    integration_provider, provider_type, file_id, current_user, token, direct_download=False
                )
                
                if download_result and download_result.get("success") and download_result.get("data"):
                    presigned_url = download_result["data"]
                    print(f"[FILE DOWNLOAD] Got presigned URL for {file_id}")
                    
                    # Download file contents from presigned URL
                    response = requests.get(presigned_url, timeout=30)
                    if response.ok:
                        file_contents = response.content
                        print(f"[FILE DOWNLOAD] Downloaded {file_id} ({len(file_contents)} bytes)")
                    else:
                        print(f"[FILE ERROR] Failed to download from presigned URL: {response.status_code}")
                        file_contents = None
                else:
                    print(f"[FILE ERROR] Failed to prepare download link for {file_id}: {download_result}")
                    file_contents = None
                
                if file_contents:
                    # Get file metadata for proper naming and typing
                    file_info = get_file_metadata_from_provider(file_id, provider_type, token, integration_provider)
                    
                    if file_info:
                        # Upload to our datasource
                        upload_result = upload_file_to_datasource(
                            token, file_info, file_contents, file_metadata["type"], current_user
                        )
                        
                        if upload_result:
                            # Delete old datasource if it exists
                            if file_metadata.get("datasource") and file_metadata["datasource"].get("id"):
                                print(f"[FILE CLEANUP] Deleting old datasource for {file_id}")
                                delete_old_datasource(token, file_metadata["datasource"]["id"])
                            
                            # Update metadata
                            result_metadata = {
                                "type": file_metadata["type"],
                                "lastCaptured": get_current_iso_timestamp(),
                                "datasource": upload_result
                            }
                            updated_files[file_id] = result_metadata
                            cache_updates[file_id] = result_metadata  # Add to cache
                            print(f"[FILE SUCCESS] Successfully updated {file_id}")
                            updated_count += 1
                        else:
                            print(f"[FILE ERROR] Upload failed for {file_id}")
                            updated_files[file_id] = file_metadata
                            error_count += 1
                    else:
                        print(f"[FILE ERROR] Could not get metadata for {file_id}")
                        updated_files[file_id] = file_metadata
                        error_count += 1
                else:
                    print(f"[FILE ERROR] Could not get contents for {file_id}")
                    updated_files[file_id] = file_metadata
                    error_count += 1
            else:
                # No update needed, keep original
                print(f"[FILE SKIP] {file_id} - No update needed")
                updated_files[file_id] = file_metadata
                cache_updates[file_id] = file_metadata  # Add to cache even if skipped
                skipped_count += 1
                
        except Exception as e:
            print(f"[FILE ERROR] Exception processing file {file_id}: {e}")
            updated_files[file_id] = file_metadata
            error_count += 1
    
    print(f"[FILES SUMMARY] Processed: {processed_count} | Updated: {updated_count} | Skipped: {skipped_count} | Cached: {cached_count} | Errors: {error_count}")
    return updated_files, cache_updates


def process_folders_with_cache(folders_data, provider_type, token, current_user, integration_provider, processed_files_cache):
    """Process folders and their contained files for syncing (with nested folder support)."""
    updated_folders = {}
    total_folders = len(folders_data)
    processed_count = 0
    
    print(f"[FOLDERS START] Processing {total_folders} folders")
    
    for folder_id in folders_data.keys():
        folder_files = folders_data[folder_id]
        processed_count += 1
        
        try:
            print(f"[FOLDER {processed_count}/{total_folders}] Processing: {folder_id}")
            
            # Get ALL files from folder and subfolders (flattened)
            current_folder_files = get_all_files_recursively(folder_id, provider_type, token, None, integration_provider)
            
            if current_folder_files is None:
                print(f"[FOLDER ERROR] Could not list files in folder {folder_id}")
                updated_folders[folder_id] = folder_files
                continue
            
            # Track which files we've processed
            visited_files = set()
            updated_folder_files = {}
            existing_files = len(folder_files)
            current_files = len(current_folder_files)
            updated_files_count = 0
            new_files_count = 0
            deleted_files_count = 0
            
            print(f"[FOLDER] {folder_id} - Existing: {existing_files}, Current: {current_files}")
            
            # DEBUG: Log the current folder contents
            print(f"[FOLDER DEBUG] {folder_id} - Found {len(current_folder_files)} files in folder tree")
            
            # Process each file currently in the folder (and subfolders)
            for provider_file in current_folder_files:
                file_id = get_file_id(provider_file)
                if not file_id:
                    continue
                    
                visited_files.add(file_id)
                
                # Check global cache first
                if file_id in processed_files_cache:
                    cached_result = processed_files_cache[file_id]
                    cache_source = "uploaded" if cached_result.get("datasource") else "skipped"
                    print(f"[FOLDER CACHED] {file_id} - Already {cache_source} globally, reusing result (saved download + upload)")
                    updated_folder_files[file_id] = cached_result
                    continue
                
                if file_id in folder_files:
                    # Existing file - check if needs update
                    existing_metadata = folder_files[file_id]
                    needs_update = should_update_file(existing_metadata, file_id, provider_type, token, integration_provider)
                    
                    if needs_update:
                        updated_file = process_single_file_with_cache(
                            file_id, existing_metadata, provider_file,
                            provider_type, token, current_user, integration_provider, processed_files_cache
                        )
                        updated_files_count += 1
                        updated_folder_files[file_id] = updated_file
                    else:
                        updated_folder_files[file_id] = existing_metadata
                        processed_files_cache[file_id] = existing_metadata  # Cache for consistency
                else:
                    # New file (could be from nested folder) - upload it
                    new_files_count += 1
                    print(f"[FOLDER] New file found: {file_id}")
                    new_file_metadata = {
                        "type": determine_file_type(provider_file),
                        "lastCaptured": None,
                        "datasource": None
                    }
                    updated_file = process_single_file_with_cache(
                        file_id, new_file_metadata, provider_file,
                        provider_type, token, current_user, integration_provider, processed_files_cache
                    )
                    # Only add file if it has required fields
                    if updated_file and updated_file.get("type"):
                        updated_folder_files[file_id] = updated_file
                    else:
                        print(f"[FOLDER ERROR] Invalid file result for {file_id}, skipping")
            
            # Handle files that are in our data but no longer in folder tree (deleted)
            for file_id in folder_files.keys():
                if file_id not in visited_files:
                    deleted_files_count += 1
                    print(f"[FOLDER] File deleted from tree: {file_id}")
                    if folder_files[file_id].get("datasource") and folder_files[file_id]["datasource"].get("id"):
                        delete_old_datasource(token, folder_files[file_id]["datasource"]["id"])
                    # Don't add to updated_folder_files (effectively removes it)
            
            # Only add folder if it contains files (to prevent schema validation errors)
            if updated_folder_files:
                updated_folders[folder_id] = updated_folder_files
                print(f"[FOLDER COMPLETE] {folder_id} - Updated: {updated_files_count}, New: {new_files_count}, Deleted: {deleted_files_count}")
            else:
                print(f"[FOLDER EMPTY] {folder_id} - No files, skipping to prevent schema errors")
                print(f"[FOLDER DEBUG] {folder_id} - Original had {len(folder_files)} files, found {len(current_folder_files)} files in tree")
            
        except Exception as e:
            print(f"[FOLDER ERROR] Failed processing folder {folder_id}: {e}")
            # Don't add empty folders that would fail schema validation
            if folder_files:  # Only keep non-empty folders
                updated_folders[folder_id] = folders_data[folder_id]
    
    print(f"[FOLDERS SUMMARY] Processed {processed_count}/{total_folders} folders")
    return updated_folders


def process_single_file_with_cache(file_id, file_metadata, provider_file, provider_type, token, current_user, integration_provider, processed_files_cache):
    """Process a single file for upload/update with cache awareness."""
    try:
        # Check cache first
        if file_id in processed_files_cache:
            cached_result = processed_files_cache[file_id]
            cache_source = "uploaded" if cached_result.get("datasource") else "skipped"
            print(f"[SINGLE CACHED] {file_id} - Already {cache_source}, reusing result (saved download + upload)")
            return cached_result
        
        print(f"[SINGLE FILE] Processing {file_id}")
        
        # Use prepare_download_link to handle file conversion and get presigned URL
        download_result = prepare_download_link(
            integration_provider, provider_type, file_id, current_user, token, direct_download=False
        )
        
        if download_result and download_result.get("success") and download_result.get("data"):
            presigned_url = download_result["data"]
            print(f"[SINGLE FILE] Got presigned URL for {file_id}")
            
            # Download file contents from presigned URL
            response = requests.get(presigned_url, timeout=30)
            if response.ok:
                file_contents = response.content
                print(f"[SINGLE FILE] Downloaded {file_id} ({len(file_contents)} bytes)")
            else:
                print(f"[SINGLE FILE ERROR] Failed to download from presigned URL: {response.status_code}")
                file_contents = None
        else:
            print(f"[SINGLE FILE ERROR] Failed to prepare download link for {file_id}: {download_result}")
            file_contents = None
        
        if file_contents:
            # Create file info from provider data
            file_info = format_provider_file_info(provider_file, provider_type)
            
            upload_result = upload_file_to_datasource(
                token, file_info, file_contents, file_metadata["type"], current_user
            )
            
            if upload_result:
                # Delete old datasource if it exists
                if file_metadata.get("datasource") and file_metadata["datasource"].get("id"):
                    print(f"[SINGLE FILE] Deleting old datasource for {file_id}")
                    delete_old_datasource(token, file_metadata["datasource"]["id"])
                
                result_metadata = {
                    "type": file_metadata["type"],
                    "lastCaptured": get_current_iso_timestamp(),
                    "datasource": upload_result
                }
                processed_files_cache[file_id] = result_metadata  # Cache the result
                print(f"[SINGLE FILE] Successfully processed {file_id}")
                return result_metadata
            else:
                print(f"[SINGLE FILE] Upload failed for {file_id}")
        else:
            print(f"[SINGLE FILE] Could not get contents for {file_id}")
        
        # Don't cache failed files - allow retry in different contexts
        return file_metadata
        
    except Exception as e:
        print(f"[SINGLE FILE] Error processing {file_id}: {e}")
        # Don't cache failed files - allow retry in different contexts
        return file_metadata


def should_update_file(file_metadata, file_id, provider_type, token, integration=None):
    """Check if a file needs to be updated based on lastCaptured vs lastModified."""
    if not file_metadata.get("lastCaptured"):
        print(f"[FILE CHECK] {file_id} - Never captured, needs update")
        return True  # Never captured before
    
    try:
        # Get file's last modified date from provider
        print(f"[TIMESTAMP DEBUG] {file_id} - Getting metadata from {provider_type}")
        provider_file_info = get_file_metadata_from_provider(file_id, provider_type, token, integration)
        print(f"[TIMESTAMP DEBUG] {file_id} - Raw provider response: {provider_file_info}")
        
        if provider_file_info and (provider_file_info.get("lastModified") or provider_file_info.get("modifiedTime") or provider_file_info.get("lastModifiedDateTime")):
            last_captured = datetime.fromisoformat(file_metadata["lastCaptured"].replace('Z', '+00:00'))
            # Get the correct field name from provider response
            last_modified_str = provider_file_info.get("lastModified") or provider_file_info.get("modifiedTime") or provider_file_info.get("lastModifiedDateTime")
            last_modified = datetime.fromisoformat(last_modified_str.replace('Z', '+00:00'))
            
            print(f"[TIMESTAMP DEBUG] {file_id} - Raw lastCaptured: {file_metadata['lastCaptured']}")
            print(f"[TIMESTAMP DEBUG] {file_id} - Raw lastModified: {last_modified_str}")
            print(f"[TIMESTAMP DEBUG] {file_id} - Parsed lastCaptured: {last_captured.isoformat()}")
            print(f"[TIMESTAMP DEBUG] {file_id} - Parsed lastModified: {last_modified.isoformat()}")
            
            needs_update = last_modified > last_captured
            
            if needs_update:
                print(f"[FILE CHECK] {file_id} - NEEDS UPDATE: Modified {last_modified.isoformat()} > Captured {last_captured.isoformat()}")
            else:
                print(f"[FILE CHECK] {file_id} - NO UPDATE NEEDED: Modified {last_modified.isoformat()} <= Captured {last_captured.isoformat()}")
            
            return needs_update
        else:
            print(f"[TIMESTAMP DEBUG] {file_id} - No timestamp fields found in provider response")
        
    except Exception as e:
        print(f"[FILE CHECK ERROR] Error checking {file_id}: {e}")
        import traceback
        print(f"[FILE CHECK ERROR] Traceback: {traceback.format_exc()}")
    
    print(f"[FILE CHECK] {file_id} - Cannot determine, skipping update")
    return False  # Default to no update if can't determine


def list_files_in_folder(folder_id, provider_type, token, integration=None):
    """Get list of files in a folder from the provider."""
    try:
        if provider_type == IntegrationType.GOOGLE:
            return execute_request(token, "/google/integrations/list_files", {"folderId": folder_id})
        elif provider_type == IntegrationType.MICROSOFT:
            # Handle both microsoft_drive and microsoft_sharepoint
            if integration == "microsoft_sharepoint":
                level, site_id, drive_id, folder_path = parse_sharepoint_folder_id(folder_id)
                
                if level == "files" and site_id and drive_id:
                    files = execute_request(token, "/microsoft/integrations/list_library_files", 
                                          {"site_id": site_id, "drive_id": drive_id, "folder_path": folder_path, "top": 100})
                    if files:
                        return format_sharepoint_files_with_folder_context(files, site_id, drive_id, folder_path)
                print(f"Cannot list files for SharePoint navigation level: {level}")
                return None
            else:
                # Default to drive (microsoft_drive or backwards compatibility)
                return execute_request(token, "/microsoft/integrations/list_drive_items", 
                                     {"folder_id": folder_id, "page_size": 100})
    except Exception as e:
        print(f"Error listing files in folder {folder_id}: {e}")
    
    return None


def get_file_metadata_from_provider(file_id, provider_type, token, integration=None):
    """Get file metadata from provider for lastModified comparison."""
    try:
        if provider_type == IntegrationType.GOOGLE:
            result = execute_request(token, "/google/integrations/get_file_metadata", {"fileId": file_id})
        elif provider_type == IntegrationType.MICROSOFT:
            # Handle both microsoft_drive and microsoft_sharepoint
            if integration == "microsoft_sharepoint":
                site_id, drive_id, item_id, error = parse_sharepoint_file_id(file_id)
                if error:
                    print(f"SharePoint file_id error: {error}")
                    return None
                result = execute_request(token, "/microsoft/integrations/get_sharepoint_drive_item_metadata", 
                                       {"site_id": site_id, "drive_id": drive_id, "item_id": item_id})
            else:
                # Default to drive (microsoft_drive or backwards compatibility)
                result = execute_request(token, "/microsoft/integrations/get_drive_item", {"item_id": file_id})
        else:
            return None
        
        # Handle different return formats
        if result:
            if isinstance(result, list):
                # Google Drive format: [id, name, mimeType, createdTime, modifiedTime, size]
                if len(result) >= 2:
                    metadata = {
                        "id": result[0] if len(result) > 0 else file_id,
                        "name": result[1] if len(result) > 1 else "unknown",
                        "mimeType": result[2] if len(result) > 2 else "application/octet-stream"
                    }
                    # Add optional fields if they exist
                    if len(result) > 3:
                        created_time_raw = result[3]
                        # Clean any prefix like 'createdTime='
                        if isinstance(created_time_raw, str) and '=' in created_time_raw:
                            metadata["createdTime"] = created_time_raw.split('=', 1)[1]
                        else:
                            metadata["createdTime"] = created_time_raw
                    if len(result) > 4:
                        modified_time_raw = result[4]
                        # Clean any prefix like 'modifiedTime='
                        if isinstance(modified_time_raw, str) and '=' in modified_time_raw:
                            metadata["modifiedTime"] = modified_time_raw.split('=', 1)[1]
                        else:
                            metadata["modifiedTime"] = modified_time_raw
                    if len(result) > 5:
                        size_raw = result[5]
                        # Clean any prefix like 'size='
                        if isinstance(size_raw, str) and '=' in size_raw:
                            metadata["size"] = size_raw.split('=', 1)[1]
                        else:
                            metadata["size"] = size_raw
                    
                    print(f"[METADATA] Converted list to dict for {file_id}: {metadata}")
                    return metadata
                else:
                    print(f"[METADATA ERROR] List too short for {file_id}: {result}")
                    return None
            elif isinstance(result, dict):
                # Already in dictionary format (Microsoft or newer Google format)
                print(f"[METADATA] Got dict format for {file_id}")
                return result
            else:
                print(f"[METADATA ERROR] Unexpected format for {file_id}: {type(result)}")
                return None
        
        return None
    except Exception as e:
        print(f"[METADATA ERROR] Exception getting metadata for file {file_id}: {e}")
        return None


def upload_file_to_datasource(token, file_info, file_contents, file_type, current_user):
    """Upload file to datasource and return AttachedDocument structure."""
    try:
        file_name = file_info.get("name", "unknown_file")
        mime_type = file_info.get("mimeType", "application/octet-stream")
        file_size = len(file_contents) if isinstance(file_contents, (str, bytes)) else "unknown"
        
        print(f"[UPLOAD] Starting upload: {file_name} ({file_size} bytes, {mime_type})")
        
        upload_result = upload_file(
            access_token=token,
            file_name=file_name,
            file_contents=file_contents,
            file_type=mime_type,
            tags=["drive-integration", file_type],
            data_props={
                "type": "assistant-drive-integration-file",
                "originalFileId": file_info.get("id"),
                "originalMimeType": mime_type,
                "syncedAt": get_current_iso_timestamp()
            },
            enter_rag_pipeline=True,
            groupId=None
        )
        
        if upload_result and upload_result.get("id"):
            print(f"[UPLOAD SUCCESS] {file_name} uploaded as {upload_result['id']}")
            datasource_obj = {
                "id": upload_result["id"],
                "name": upload_result["name"],
                "raw": None,
                "type": upload_result["type"],
                "data": upload_result.get("data"),
                "key": upload_result["id"],
                "metadata": upload_result.get("data")
            }
            # Only include groupId if it's a valid string
            group_id = upload_result.get("groupId")
            if group_id:
                datasource_obj["groupId"] = group_id
            
            return datasource_obj
        else:
            print(f"[UPLOAD FAILED] {file_name} - No result or ID returned")
        
    except Exception as e:
        print(f"[UPLOAD ERROR] Failed uploading {file_info.get('name', 'unknown')}: {e}")
    
    return None


def delete_old_datasource(token, datasource_id):
    """Delete old datasource file."""
    try:
        delete_file(token, datasource_id)
        print(f"[CLEANUP] Deleted old datasource: {datasource_id}")
    except Exception as e:
        print(f"[CLEANUP ERROR] Failed deleting datasource {datasource_id}: {e}")
        # Don't fail the whole operation if delete fails


def determine_file_type(provider_file):
    """Determine file type from provider file data."""
    if isinstance(provider_file, dict):
        return provider_file.get("mimeType", "file")
    elif isinstance(provider_file, list) and len(provider_file) > 2:
        return provider_file[2]  # Google Drive format
    return "file"


def format_provider_file_info(provider_file, provider_type):
    """Format provider file data into consistent structure."""
    if isinstance(provider_file, dict):
        return provider_file
    elif isinstance(provider_file, list):
        # Google Drive format [id, name, mimeType, ...]
        return {
            "id": provider_file[0] if len(provider_file) > 0 else None,
            "name": provider_file[1] if len(provider_file) > 1 else "unknown",
            "mimeType": provider_file[2] if len(provider_file) > 2 else "application/octet-stream"
        }
    return {"id": None, "name": "unknown", "mimeType": "application/octet-stream"}


def get_current_iso_timestamp():
    """Get current timestamp in ISO format."""
    return datetime.utcnow().isoformat() + 'Z'


def is_folder(provider_file, provider_type):
    """Check if a provider file is a folder."""
    mime_type = ""
    
    if isinstance(provider_file, dict):
        mime_type = provider_file.get("mimeType", "") or provider_file.get("type", "")
    elif isinstance(provider_file, list) and len(provider_file) > 2:
        # Google Drive format [id, name, mimeType, ...]
        mime_type = provider_file[2]
    
    return "folder" in mime_type.lower()


def get_file_id(provider_file):
    """Extract file/folder ID from provider file data."""
    if isinstance(provider_file, dict):
        return provider_file.get("id")
    elif isinstance(provider_file, list) and len(provider_file) > 0:
        return provider_file[0]
    return None


def get_all_files_recursively(folder_id, provider_type, token, visited_folders=None, integration=None):
    """
    Recursively get all files from folder and subfolders, flattened.
    Returns a flat list of all files (no nested folder structure).
    """
    if visited_folders is None:
        visited_folders = set()
    
    # Prevent infinite recursion
    if folder_id in visited_folders:
        print(f"[FOLDER] Skipping already visited folder: {folder_id}")
        return []
    visited_folders.add(folder_id)
    
    all_files = []
    
    try:
        print(f"[FOLDER] Listing contents of folder: {folder_id}")
        folder_contents = list_files_in_folder(folder_id, provider_type, token, integration)
        
        if folder_contents:
            folders_found = 0
            files_found = 0
            
            for item in folder_contents:
                if is_folder(item, provider_type):
                    folders_found += 1
                    # Recursively get files from subfolder
                    subfolder_id = get_file_id(item)
                    if subfolder_id:
                        print(f"[FOLDER] Processing nested folder: {subfolder_id}")
                        # For SharePoint, we need to build the proper path
                        if integration == "microsoft_sharepoint":
                            # Extract site_id and drive_id from current folder context
                            level, current_site_id, current_drive_id, current_folder_path = parse_sharepoint_folder_id(folder_id)
                            if level == "files" and current_site_id and current_drive_id:
                                # Build subfolder path
                                if current_folder_path == "root":
                                    subfolder_path = item['name']
                                else:
                                    subfolder_path = f"{current_folder_path}/{item['name']}"
                                subfolder_full_id = f"{current_site_id}:{current_drive_id}:{subfolder_path}"
                                subfolder_files = get_all_files_recursively(
                                    subfolder_full_id, provider_type, token, visited_folders, integration
                                )
                            else:
                                subfolder_files = []
                        else:
                            subfolder_files = get_all_files_recursively(
                                subfolder_id, provider_type, token, visited_folders, integration
                            )
                        all_files.extend(subfolder_files)
                        print(f"[FOLDER] Found {len(subfolder_files)} files in nested folder {subfolder_id}")
                else:
                    # Regular file - add to collection
                    files_found += 1
                    all_files.append(item)
            
            print(f"[FOLDER] {folder_id} contains {files_found} files and {folders_found} subfolders")
        else:
            print(f"[FOLDER] {folder_id} is empty or inaccessible")
        
    except Exception as e:
        print(f"[FOLDER ERROR] Error processing folder {folder_id}: {e}")
    
    return all_files


def filter_to_root_level_items(file_list_result):
    """
    Filters a list of file results to show only root-level items.
    Simple two-pass approach: collect all folder IDs, then filter out items whose parents are folders in the list.
    """
    # First pass: collect all folder IDs
    folder_ids = set()
    
    for i, item in enumerate(file_list_result):
        item_name = item[1] if len(item) > 1 else "unknown"
        mime_type = item[2] if len(item) > 2 else "unknown"
        
        if mime_type == "application/vnd.google-apps.folder":
            folder_id = item[0]
            folder_ids.add(folder_id)
        # Skip logging for non-folders during collection phase
    
    print(f"Found {len(folder_ids)} folders in listing")
    
    # Second pass: filter items
    root_level_items = []
    
    for i, item in enumerate(file_list_result):
        item_name = item[1] if len(item) > 1 else "unknown"
        mime_type = item[2] if len(item) > 2 else "unknown"
        parents = item[3] if len(item) > 3 else []  # Get parents array
        
        # Always include files (non-folders) - no detailed logging
        if mime_type != "application/vnd.google-apps.folder":
            root_level_items.append(item)
            continue
        
        # For folders, check parent logic
        if not parents:
            print(f"Filtered out (empty parents): {item_name}")
        else:
            has_folder_parent_in_listing = any(parent_id in folder_ids for parent_id in parents)
            
            if has_folder_parent_in_listing:
                print(f"Filtered out (subfolder): {item_name}")
            else:
                print(f"Root-level folder: {item_name}")
                root_level_items.append(item)
    
    print(f"Showing {len(root_level_items)} root-level items (filtered out {len(file_list_result) - len(root_level_items)})")
    
    return root_level_items


