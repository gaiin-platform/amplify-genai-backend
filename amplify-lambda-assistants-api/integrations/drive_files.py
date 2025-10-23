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

from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    S3Operation
)
from pycommon.authz import validated, setup_validated
from pycommon.api.files import upload_file, delete_file
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

from pycommon.logger import getLogger
logger = getLogger("assistants_api_drive_files")

API_URL = os.environ["API_BASE_URL"]


# unifies location for functions needed in datasource file manager component.
@validated("list_files")
def list_integration_files(event, context, current_user, name, data):
    token = data["access_token"]
    data = data["data"]
    integration = data["integration"]
    integration_provider = provider_case(integration)
    folder_id = data.get("folder_id")

    logger.info("Listing files for integration: %s", integration_provider)
    result = list_files(integration_provider, token, folder_id)
    if result:
        return {"success": True, "data": result}

    return {"success": False, "error": "No integration files found"}


def list_files(integration_provider, token, folder_id=None):
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
                logger.debug("Google list_files result: %s", result)
                
                # If no folder_id specified, filter to show only root-level items
                if not folder_id:
                    logger.debug("Filtering to root-level items only")
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
            return execute_request(
                token,
                "/microsoft/integrations/list_drive_items",
                {"folder_id": folder_id if folder_id else "root", "page_size": 100},
            )

    logger.warning("No result from list_files for integration: %s", integration_provider)
    return None


@required_env_vars({
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT],
})
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
    logger.info("Starting download for integration %s, file %s", integration_provider, file_id)
    result = request_download_link(integration_provider, file_id, token)
    logger.debug("Download link result: %s", result)

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
                logger.debug(
                    "File name: %s, mime type: %s, extension: %s", file_name, file_mime_type, file_extension
                )
                safe_file_name = re.sub(r"[^a-zA-Z0-9._-]", "", file_name)

                if "." in safe_file_name:
                    safe_file_name = safe_file_name.rsplit(".", 1)[0]

                safe_file_name += file_extension

                credentials = get_user_credentials(current_user, integration)
                logger.debug(
                    "Downloading file: %s, mime type: %s, safe name: %s", file_name, file_mime_type, safe_file_name
                )
                file_content = get_file_contents(
                    integration_provider, credentials, download_file_id, download_url
                )
                if not file_content:
                    return {"success": False, "error": "Failed to get file contents"}

                # Create an S3 key using the safe file name; no double extension.
                key = f"tempIntegrationFiles/{current_user}/{safe_file_name}"

                bucket = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
                logger.debug("Saving file to S3 bucket: %s, key: %s", bucket, key)

                try:
                    s3 = boto3.client("s3")
                    s3.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=file_content,
                        ContentType=file_mime_type,
                    )
                    logger.info("File successfully saved to S3")
                except Exception as s3_error:
                    logger.error("S3 upload error details: %s", s3_error)
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
            logger.error("Error saving file to S3: %s", e)
            return {"success": False, "error": f"Error saving file to S3: {str(e)}"}
    else:
        logger.warning("No download link in result: %s", result)
        return {"success": False, "error": "Failed to get download link for file"}


def request_download_link(integration_provider, file_id, token):
    """
    Downloads a file from the integration.
    """
    match integration_provider:
        case IntegrationType.GOOGLE:
            return execute_request(
                token, "/google/integrations/get_download_link", {"fileId": file_id}
            )
        case IntegrationType.MICROSOFT:
            return execute_request(
                token, "/microsoft/integrations/download_file", {"item_id": file_id}
            )


def get_file_contents(integration_provider, credentials, file_id, download_url):
    logger.debug(
        "Getting file contents for integration: %s, file_id: %s", integration_provider, file_id
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
                    logger.error(
                        "Error downloading Microsoft file: HTTP %s - %s", response.status_code, response.reason
                    )
                    return None

                # Use BytesIO to accumulate streamed content
                file_content = BytesIO()
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_content.write(chunk)

                return file_content.getvalue()
    except Exception as e:
        logger.error(
            "Error getting file contents for integration: %s - error: %s", integration_provider, e
        )
        return None


def execute_request(access_token, url_path, data):
    logger.info("Executing request to %s", url_path)
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
            logger.error("Error executing request: %s", response_content)
            return None
        elif response.status_code == 200 and response_content.get("success", False):
            logger.info("Successfully executed request: %s", url_path)
            return response_content.get("data", None)

    except Exception as e:
        logger.error("Error updating permissions: %s", e)
        return None


def cleanup_after_download_file(integration_provider, download_file_id, token):
    match integration_provider:
        case IntegrationType.GOOGLE:
            # Clean up converted file if it was created during this process
            try:
                logger.info("Cleaning up converted file with ID: %s", download_file_id)
                cleanup_result = execute_request(
                    token,
                    "/google/integrations/delete_item_permanently",
                    {"itemId": download_file_id},
                )
                if cleanup_result:
                    logger.info("Successfully deleted converted file")
                else:
                    logger.warning("Failed to delete converted file")
            except Exception as e:
                logger.error("Error deleting converted file: %s, continuing...", e)
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


@required_env_vars({
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT],
})
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
        
        logger.info("Processing drive files to data sources for user: %s", current_user)
        start_time = time.time()
        
        # Create a deep copy of the payload to return with updates
        updated_payload = copy.deepcopy(payload)
        
        # Global cache to track processed files across ALL providers and sections
        processed_files_cache = {}  # file_id -> file_metadata
        
        # Create tasks for each provider to run concurrently
        provider_tasks = []
        provider_names = []
        
        for integration_provider in payload.keys():
            logger.debug("Creating async task for provider: %s", integration_provider)
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
        logger.info("[ASYNC] Starting concurrent processing of %s providers", len(provider_tasks))
        provider_results = await asyncio.gather(*provider_tasks, return_exceptions=True)
        
        # Process results and handle any exceptions
        for i, (provider_name, result) in enumerate(zip(provider_names, provider_results)):
            if isinstance(result, Exception):
                logger.error("Error processing provider %s: %s", provider_name, result)
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
        
        logger.info("[ASYNC COMPLETE] Total processing time: %.2fs", end_time - start_time)
        logger.info("[CACHE STATS] Total unique files processed: %s", total_cached)
        logger.info("[CACHE STATS] Results - Uploaded: %s | Skipped (no update needed): %s", successful_cached, skipped_cached)
        if total_cached > 0:
            logger.info("[CACHE STATS] Cache efficiency: Prevented duplicate processing for any overlapping files")
        
        return {"success": True, "data": updated_payload}
        
    except Exception as e:
        logger.error("Error in async drive_files_to_data_sources: %s", e)
        return {"success": False, "error": str(e)}


async def _process_provider_async(integration_provider: str, provider_data: Dict, token: str, current_user: str, processed_files_cache: Dict) -> Dict:
    """
    Process a single integration provider asynchronously.
    Returns updated provider data structure.
    """
    try:
        logger.info("[ASYNC] Processing provider: %s", integration_provider)
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
                    logger.info("[DEDUP] %s: Removed %s files from files section (appear in folders)", integration_provider, removed_count)
        
        provider_end = time.time()
        logger.info("[ASYNC] %s completed in %.2fs", integration_provider, provider_end - provider_start)
        
        return updated_provider_data
        
    except Exception as e:
        logger.error("[ASYNC] Error processing provider %s: %s", integration_provider, e)
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
    
    logger.info("[FILES START] Processing %s files for %s", total_files, integration_provider)
    
    for file_id in files_data.keys():
        file_metadata = files_data[file_id]
        processed_count += 1
        
        try:
            # Check if file was already processed
            if file_id in processed_files_cache:
                cached_result = processed_files_cache[file_id]
                cache_source = "uploaded" if cached_result.get("datasource") else "skipped"
                logger.debug("[FILE CACHED] %s - Already %s, reusing result (saved download + upload)", file_id, cache_source)
                updated_files[file_id] = cached_result
                cache_updates[file_id] = cached_result  # Include cached files in updates
                cached_count += 1
                continue
            
            logger.debug("[FILE CHECK] %s - %s", file_id, file_metadata.get('lastCaptured', 'Never captured'))
            
            # Check if file needs to be processed
            needs_update = should_update_file(file_metadata, file_id, provider_type, token)
            
            if needs_update:
                logger.info("[FILE %s/%s] Updating %s", processed_count, total_files, file_id)
                
                # Use prepare_download_link to handle file conversion and get presigned URL
                download_result = prepare_download_link(
                    integration_provider, provider_type, file_id, current_user, token, direct_download=False
                )
                
                if download_result and download_result.get("success") and download_result.get("data"):
                    presigned_url = download_result["data"]
                    logger.debug("[FILE DOWNLOAD] Got presigned URL for %s", file_id)
                    
                    # Download file contents from presigned URL
                    response = requests.get(presigned_url, timeout=30)
                    if response.ok:
                        file_contents = response.content
                        logger.debug("[FILE DOWNLOAD] Downloaded %s (%s bytes)", file_id, len(file_contents))
                    else:
                        logger.error("[FILE ERROR] Failed to download from presigned URL: %s", response.status_code)
                        file_contents = None
                else:
                    logger.error("[FILE ERROR] Failed to prepare download link for %s: %s", file_id, download_result)
                    file_contents = None
                
                if file_contents:
                    # Get file metadata for proper naming and typing
                    file_info = get_file_metadata_from_provider(file_id, provider_type, token)
                    
                    if file_info:
                        # Upload to our datasource
                        upload_result = upload_file_to_datasource(
                            token, file_info, file_contents, file_metadata["type"], current_user
                        )
                        
                        if upload_result:
                            # Delete old datasource if it exists
                            if file_metadata.get("datasource") and file_metadata["datasource"].get("id"):
                                logger.info("[FILE CLEANUP] Deleting old datasource for %s", file_id)
                                delete_old_datasource(token, file_metadata["datasource"]["id"])
                            
                            # Update metadata
                            result_metadata = {
                                "type": file_metadata["type"],
                                "lastCaptured": get_current_iso_timestamp(),
                                "datasource": upload_result
                            }
                            updated_files[file_id] = result_metadata
                            cache_updates[file_id] = result_metadata  # Add to cache
                            logger.info("[FILE SUCCESS] Successfully updated %s", file_id)
                            updated_count += 1
                        else:
                            logger.error("[FILE ERROR] Upload failed for %s", file_id)
                            updated_files[file_id] = file_metadata
                            error_count += 1
                    else:
                        logger.error("[FILE ERROR] Could not get metadata for %s", file_id)
                        updated_files[file_id] = file_metadata
                        error_count += 1
                else:
                    logger.error("[FILE ERROR] Could not get contents for %s", file_id)
                    updated_files[file_id] = file_metadata
                    error_count += 1
            else:
                # No update needed, keep original
                logger.debug("[FILE SKIP] %s - No update needed", file_id)
                updated_files[file_id] = file_metadata
                cache_updates[file_id] = file_metadata  # Add to cache even if skipped
                skipped_count += 1
                
        except Exception as e:
            logger.error("[FILE ERROR] Exception processing file %s: %s", file_id, e)
            updated_files[file_id] = file_metadata
            error_count += 1
    
    logger.info("[FILES SUMMARY] Processed: %s | Updated: %s | Skipped: %s | Cached: %s | Errors: %s", processed_count, updated_count, skipped_count, cached_count, error_count)
    return updated_files, cache_updates


def process_folders_with_cache(folders_data, provider_type, token, current_user, integration_provider, processed_files_cache):
    """Process folders and their contained files for syncing (with nested folder support)."""
    updated_folders = {}
    total_folders = len(folders_data)
    processed_count = 0
    
    logger.info("[FOLDERS START] Processing %s folders", total_folders)
    
    for folder_id in folders_data.keys():
        folder_files = folders_data[folder_id]
        processed_count += 1
        
        try:
            logger.info("[FOLDER %s/%s] Processing: %s", processed_count, total_folders, folder_id)
            
            # Get ALL files from folder and subfolders (flattened)
            current_folder_files = get_all_files_recursively(folder_id, provider_type, token)
            
            if current_folder_files is None:
                logger.error("[FOLDER ERROR] Could not list files in folder %s", folder_id)
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
            
            logger.debug("[FOLDER] %s - Existing: %s, Current: %s", folder_id, existing_files, current_files)
            
            # DEBUG: Log the current folder contents
            logger.debug("[FOLDER DEBUG] %s - Found %s files in folder tree", folder_id, len(current_folder_files))
            
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
                    logger.debug("[FOLDER CACHED] %s - Already %s globally, reusing result (saved download + upload)", file_id, cache_source)
                    updated_folder_files[file_id] = cached_result
                    continue
                
                if file_id in folder_files:
                    # Existing file - check if needs update
                    existing_metadata = folder_files[file_id]
                    needs_update = should_update_file(existing_metadata, file_id, provider_type, token)
                    
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
                    logger.info("[FOLDER] New file found: %s", file_id)
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
                        logger.error("[FOLDER ERROR] Invalid file result for %s, skipping", file_id)
            
            # Handle files that are in our data but no longer in folder tree (deleted)
            for file_id in folder_files.keys():
                if file_id not in visited_files:
                    deleted_files_count += 1
                    logger.info("[FOLDER] File deleted from tree: %s", file_id)
                    if folder_files[file_id].get("datasource") and folder_files[file_id]["datasource"].get("id"):
                        delete_old_datasource(token, folder_files[file_id]["datasource"]["id"])
                    # Don't add to updated_folder_files (effectively removes it)
            
            # Only add folder if it contains files (to prevent schema validation errors)
            if updated_folder_files:
                updated_folders[folder_id] = updated_folder_files
                logger.info("[FOLDER COMPLETE] %s - Updated: %s, New: %s, Deleted: %s", folder_id, updated_files_count, new_files_count, deleted_files_count)
            else:
                logger.warning("[FOLDER EMPTY] %s - No files, skipping to prevent schema errors", folder_id)
                logger.debug("[FOLDER DEBUG] %s - Original had %s files, found %s files in tree", folder_id, len(folder_files), len(current_folder_files))
            
        except Exception as e:
            logger.error("[FOLDER ERROR] Failed processing folder %s: %s", folder_id, e)
            # Don't add empty folders that would fail schema validation
            if folder_files:  # Only keep non-empty folders
                updated_folders[folder_id] = folders_data[folder_id]
    
    logger.info("[FOLDERS SUMMARY] Processed %s/%s folders", processed_count, total_folders)
    return updated_folders


def process_single_file_with_cache(file_id, file_metadata, provider_file, provider_type, token, current_user, integration_provider, processed_files_cache):
    """Process a single file for upload/update with cache awareness."""
    try:
        # Check cache first
        if file_id in processed_files_cache:
            cached_result = processed_files_cache[file_id]
            cache_source = "uploaded" if cached_result.get("datasource") else "skipped"
            logger.debug("[SINGLE CACHED] %s - Already %s, reusing result (saved download + upload)", file_id, cache_source)
            return cached_result
        
        logger.info("[SINGLE FILE] Processing %s", file_id)
        
        # Use prepare_download_link to handle file conversion and get presigned URL
        download_result = prepare_download_link(
            integration_provider, provider_type, file_id, current_user, token, direct_download=False
        )
        
        if download_result and download_result.get("success") and download_result.get("data"):
            presigned_url = download_result["data"]
            logger.debug("[SINGLE FILE] Got presigned URL for %s", file_id)
            
            # Download file contents from presigned URL
            response = requests.get(presigned_url, timeout=30)
            if response.ok:
                file_contents = response.content
                logger.debug("[SINGLE FILE] Downloaded %s (%s bytes)", file_id, len(file_contents))
            else:
                logger.error("[SINGLE FILE ERROR] Failed to download from presigned URL: %s", response.status_code)
                file_contents = None
        else:
            logger.error("[SINGLE FILE ERROR] Failed to prepare download link for %s: %s", file_id, download_result)
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
                    logger.info("[SINGLE FILE] Deleting old datasource for %s", file_id)
                    delete_old_datasource(token, file_metadata["datasource"]["id"])
                
                result_metadata = {
                    "type": file_metadata["type"],
                    "lastCaptured": get_current_iso_timestamp(),
                    "datasource": upload_result
                }
                processed_files_cache[file_id] = result_metadata  # Cache the result
                logger.info("[SINGLE FILE] Successfully processed %s", file_id)
                return result_metadata
            else:
                logger.error("[SINGLE FILE] Upload failed for %s", file_id)
        else:
            logger.error("[SINGLE FILE] Could not get contents for %s", file_id)
        
        # Don't cache failed files - allow retry in different contexts
        return file_metadata
        
    except Exception as e:
        logger.error("[SINGLE FILE] Error processing %s: %s", file_id, e)
        # Don't cache failed files - allow retry in different contexts
        return file_metadata


def should_update_file(file_metadata, file_id, provider_type, token):
    """Check if a file needs to be updated based on lastCaptured vs lastModified."""
    if not file_metadata.get("lastCaptured"):
        logger.debug("[FILE CHECK] %s - Never captured, needs update", file_id)
        return True  # Never captured before
    
    try:
        # Get file's last modified date from provider
        logger.debug("[TIMESTAMP DEBUG] %s - Getting metadata from %s", file_id, provider_type)
        provider_file_info = get_file_metadata_from_provider(file_id, provider_type, token)
        logger.debug("[TIMESTAMP DEBUG] %s - Raw provider response: %s", file_id, provider_file_info)
        
        if provider_file_info and (provider_file_info.get("lastModified") or provider_file_info.get("modifiedTime") or provider_file_info.get("lastModifiedDateTime")):
            last_captured = datetime.fromisoformat(file_metadata["lastCaptured"].replace('Z', '+00:00'))
            # Get the correct field name from provider response
            last_modified_str = provider_file_info.get("lastModified") or provider_file_info.get("modifiedTime") or provider_file_info.get("lastModifiedDateTime")
            last_modified = datetime.fromisoformat(last_modified_str.replace('Z', '+00:00'))
            
            logger.debug("[TIMESTAMP DEBUG] %s - Raw lastCaptured: %s", file_id, file_metadata['lastCaptured'])
            logger.debug("[TIMESTAMP DEBUG] %s - Raw lastModified: %s", file_id, last_modified_str)
            logger.debug("[TIMESTAMP DEBUG] %s - Parsed lastCaptured: %s", file_id, last_captured.isoformat())
            logger.debug("[TIMESTAMP DEBUG] %s - Parsed lastModified: %s", file_id, last_modified.isoformat())
            
            needs_update = last_modified > last_captured
            
            if needs_update:
                logger.info("[FILE CHECK] %s - NEEDS UPDATE: Modified %s > Captured %s", file_id, last_modified.isoformat(), last_captured.isoformat())
            else:
                logger.debug("[FILE CHECK] %s - NO UPDATE NEEDED: Modified %s <= Captured %s", file_id, last_modified.isoformat(), last_captured.isoformat())
            
            return needs_update
        else:
            logger.warning("[TIMESTAMP DEBUG] %s - No timestamp fields found in provider response", file_id)
        
    except Exception as e:
        logger.error("[FILE CHECK ERROR] Error checking %s: %s", file_id, e)
        import traceback
        logger.debug("[FILE CHECK ERROR] Traceback: %s", traceback.format_exc())
    
    logger.warning("[FILE CHECK] %s - Cannot determine, skipping update", file_id)
    return False  # Default to no update if can't determine


def list_files_in_folder(folder_id, provider_type, token):
    """Get list of files in a folder from the provider."""
    try:
        if provider_type == IntegrationType.GOOGLE:
            return execute_request(token, "/google/integrations/list_files", {"folderId": folder_id})
        elif provider_type == IntegrationType.MICROSOFT:
            return execute_request(token, "/microsoft/integrations/list_drive_items", 
                                 {"folder_id": folder_id, "page_size": 100})
    except Exception as e:
        logger.error("Error listing files in folder %s: %s", folder_id, e)
    
    return None


def get_file_metadata_from_provider(file_id, provider_type, token):
    """Get file metadata from provider for lastModified comparison."""
    try:
        if provider_type == IntegrationType.GOOGLE:
            result = execute_request(token, "/google/integrations/get_file_metadata", {"fileId": file_id})
        elif provider_type == IntegrationType.MICROSOFT:
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
                    
                    logger.debug("[METADATA] Converted list to dict for %s: %s", file_id, metadata)
                    return metadata
                else:
                    logger.error("[METADATA ERROR] List too short for %s: %s", file_id, result)
                    return None
            elif isinstance(result, dict):
                # Already in dictionary format (Microsoft or newer Google format)
                logger.debug("[METADATA] Got dict format for %s", file_id)
                return result
            else:
                logger.error("[METADATA ERROR] Unexpected format for %s: %s", file_id, type(result))
                return None
        
        return None
    except Exception as e:
        logger.error("[METADATA ERROR] Exception getting metadata for file %s: %s", file_id, e)
        return None


def upload_file_to_datasource(token, file_info, file_contents, file_type, current_user):
    """Upload file to datasource and return AttachedDocument structure."""
    try:
        file_name = file_info.get("name", "unknown_file")
        mime_type = file_info.get("mimeType", "application/octet-stream")
        file_size = len(file_contents) if isinstance(file_contents, (str, bytes)) else "unknown"
        
        logger.info("[UPLOAD] Starting upload: %s (%s bytes, %s)", file_name, file_size, mime_type)
        
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
            logger.info("[UPLOAD SUCCESS] %s uploaded as %s", file_name, upload_result['id'])
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
            logger.error("[UPLOAD FAILED] %s - No result or ID returned", file_name)
        
    except Exception as e:
        logger.error("[UPLOAD ERROR] Failed uploading %s: %s", file_info.get('name', 'unknown'), e)
    
    return None


def delete_old_datasource(token, datasource_id):
    """Delete old datasource file."""
    try:
        delete_file(token, datasource_id)
        logger.info("[CLEANUP] Deleted old datasource: %s", datasource_id)
    except Exception as e:
        logger.error("[CLEANUP ERROR] Failed deleting datasource %s: %s", datasource_id, e)
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


def get_all_files_recursively(folder_id, provider_type, token, visited_folders=None):
    """
    Recursively get all files from folder and subfolders, flattened.
    Returns a flat list of all files (no nested folder structure).
    """
    if visited_folders is None:
        visited_folders = set()
    
    # Prevent infinite recursion
    if folder_id in visited_folders:
        logger.debug("[FOLDER] Skipping already visited folder: %s", folder_id)
        return []
    visited_folders.add(folder_id)
    
    all_files = []
    
    try:
        logger.debug("[FOLDER] Listing contents of folder: %s", folder_id)
        folder_contents = list_files_in_folder(folder_id, provider_type, token)
        
        if folder_contents:
            folders_found = 0
            files_found = 0
            
            for item in folder_contents:
                if is_folder(item, provider_type):
                    folders_found += 1
                    # Recursively get files from subfolder
                    subfolder_id = get_file_id(item)
                    if subfolder_id:
                        logger.debug("[FOLDER] Processing nested folder: %s", subfolder_id)
                        subfolder_files = get_all_files_recursively(
                            subfolder_id, provider_type, token, visited_folders
                        )
                        all_files.extend(subfolder_files)
                        logger.debug("[FOLDER] Found %s files in nested folder %s", len(subfolder_files), subfolder_id)
                else:
                    # Regular file - add to collection
                    files_found += 1
                    all_files.append(item)
            
            logger.debug("[FOLDER] %s contains %s files and %s subfolders", folder_id, files_found, folders_found)
        else:
            logger.warning("[FOLDER] %s is empty or inaccessible", folder_id)
        
    except Exception as e:
        logger.error("[FOLDER ERROR] Error processing folder %s: %s", folder_id, e)
    
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
    
    logger.debug("Found %s folders in listing", len(folder_ids))
    
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
            logger.debug("Filtered out (empty parents): %s", item_name)
        else:
            has_folder_parent_in_listing = any(parent_id in folder_ids for parent_id in parents)
            
            if has_folder_parent_in_listing:
                logger.debug("Filtered out (subfolder): %s", item_name)
            else:
                logger.debug("Root-level folder: %s", item_name)
                root_level_items.append(item)
    
    logger.debug("Showing %s root-level items (filtered out %s)", len(root_level_items), len(file_list_result) - len(root_level_items))
    
    return root_level_items


