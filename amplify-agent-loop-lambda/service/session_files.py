import os
import hashlib
from datetime import datetime
import json
import boto3
from typing import Dict, List, Tuple, Optional
import uuid
from pathlib import Path
from botocore.exceptions import ClientError
import shutil
import requests
from pycommon.logger import getLogger
logger = getLogger("session_files")

class LambdaFileTracker:
    def __init__(self, current_user: str, session_id: str, working_dir: str = "/tmp"):
        self.current_user = current_user
        self.session_id = session_id
        self.existing_mappings: Dict[str, str] = {}
        self.initial_state: Dict[str, Dict] = {}
        self.s3_client = boto3.client("s3")
        self.consolidation_bucket = os.getenv("S3_CONSOLIDATION_BUCKET_NAME")
        self.legacy_bucket = os.getenv("AGENT_STATE_BUCKET")  # Marked for deletion
        self.data_sources: Dict[str, str] = {}  # Maps source ID -> local filename
        self.deleted_files: List[str] = []  # Track files that have been deleted

        # Create session working directory in /tmp
        self.working_dir = working_dir
        os.makedirs(self.working_dir, exist_ok=True)

    def __del__(self):
        """Cleanup temporary files when object is destroyed."""
        try:
            if os.path.exists(self.working_dir):
                shutil.rmtree(self.working_dir)
        except Exception as e:
            logger.error("Error cleaning up temporary files: %s", e)

    def get_file_info(self, filepath: str) -> Dict:
        """Get file size and hash for a given file."""
        stats = os.stat(filepath)
        with open(filepath, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        return {"size": stats.st_size, "hash": file_hash, "mtime": stats.st_mtime}

    def scan_directory(self) -> Dict[str, Dict]:
        """Scan working directory and return file information."""
        file_info = {}
        for root, _, files in os.walk(self.working_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.working_dir)
                try:
                    logger.debug("Found file %s", rel_path)
                    file_info[rel_path] = self.get_file_info(full_path)
                except (IOError, OSError) as e:
                    logger.error("Error reading file %s: %s", full_path, e)
        return file_info

    def find_existing_session(self) -> Optional[Dict]:
        """Look for existing session files in S3 with backward compatibility."""
        try:
            # Try consolidation bucket first (migrated records)
            consolidation_index_key = f"agentState/{self.current_user}/{self.session_id}/index.json"
            try:
                response = self.s3_client.get_object(Bucket=self.consolidation_bucket, Key=consolidation_index_key)
                session_data = json.loads(response["Body"].read().decode("utf-8"))
                # Store existing mappings when session is found
                self.existing_mappings = session_data.get("mappings", {})
                # Load data sources mapping
                self.data_sources = session_data.get("data_sources", {})
                # Load list of deleted files
                self.deleted_files = session_data.get("deleted_files", [])
                # Mark as using consolidation bucket
                session_data["_bucket_type"] = "consolidation"
                return session_data

            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey" and self.legacy_bucket:
                    # Fallback to legacy bucket
                    legacy_index_key = f"{self.current_user}/{self.session_id}/index.json"
                    try:
                        response = self.s3_client.get_object(Bucket=self.legacy_bucket, Key=legacy_index_key)
                        session_data = json.loads(response["Body"].read().decode("utf-8"))
                        # Store existing mappings when session is found
                        self.existing_mappings = session_data.get("mappings", {})
                        # Load data sources mapping
                        self.data_sources = session_data.get("data_sources", {})
                        # Load list of deleted files
                        self.deleted_files = session_data.get("deleted_files", [])
                        # Mark as using legacy bucket
                        session_data["_bucket_type"] = "legacy"
                        return session_data

                    except ClientError as legacy_e:
                        logger.error("Error checking for existing session in both buckets: consolidation=%s, legacy=%s", e, legacy_e)
                        if legacy_e.response["Error"]["Code"] == "NoSuchKey":
                            return None
                        raise legacy_e
                else:
                    logger.error("Error checking for existing session in consolidation bucket: %s", e)
                    if e.response["Error"]["Code"] == "NoSuchKey":
                        return None
                    raise

        except Exception as e:
            logger.error("Error checking for existing session: %s", e)
            return None

    def restore_session_files(self, index_content: Dict) -> bool:
        """Restore files from a previous session to the working directory."""
        try:
            # Determine which bucket and key prefix to use
            bucket_type = index_content.get("_bucket_type", "legacy")
            if bucket_type == "consolidation":
                bucket_to_use = self.consolidation_bucket
                key_prefix = f"agentState/{self.current_user}/{self.session_id}/"
            else:
                bucket_to_use = self.legacy_bucket
                key_prefix = f"{self.current_user}/{self.session_id}/"

            # Load list of files that were previously deleted
            deleted_files = index_content.get("deleted_files", [])
            files_restored = 0
            files_skipped = 0

            # Download each file from the index
            for original_path, s3_name in index_content["mappings"].items():
                # Skip files that were previously deleted
                if (
                    original_path in deleted_files
                    or original_path in self.deleted_files
                ):
                    logger.info("Skipping previously deleted file: %s", original_path)
                    files_skipped += 1
                    continue

                # Construct the S3 key based on bucket type
                s3_key = f"{key_prefix}{s3_name}"
                logger.info("Restoring %s from %s (bucket: %s)", original_path, s3_key, bucket_to_use)
                local_path = os.path.join(self.working_dir, original_path)

                # Ensure the target directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                try:
                    # Download the file
                    self.s3_client.download_file(bucket_to_use, s3_key, local_path)
                    files_restored += 1
                except ClientError as e:
                    logger.error("Error downloading file %s: %s", s3_key, e)
                    continue

            logger.info(
                "Files restored: %d, files skipped (previously deleted): %d", files_restored, files_skipped
            )
            return True

        except Exception as e:
            logger.error("Error restoring session files: %s", e)
            return False

    def write_file(self, filename: str, content: bytes | str) -> str:
        """
        Write content to a file in the working directory.
        Returns the relative path to the file.
        """
        local_path = os.path.join(self.working_dir, filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        mode = "wb" if isinstance(content, bytes) else "w"
        with open(local_path, mode) as f:
            f.write(content)

        return filename

    def read_file(self, filename: str) -> bytes:
        """Read content from a file in the working directory."""
        local_path = os.path.join(self.working_dir, filename)
        with open(local_path, "rb") as f:
            return f.read()

    def start_tracking(self) -> Dict:
        """Start tracking files, optionally restoring from a previous session."""
        logger.info("Checking for existing session")

        existing_session = self.find_existing_session()

        if existing_session:
            logger.info("Found existing session:")

            # Print the full existing session details
            logger.debug("%s", json.dumps(existing_session, indent=2))

            if self.restore_session_files(existing_session):
                logger.info("Successfully restored session files")
            else:
                logger.warning("Failed to restore some session files")
        else:
            logger.info("No existing session found, starting fresh tracking")

        # Start tracking current state
        self.initial_state = self.scan_directory()
        logger.info("Now tracking %d files", len(self.initial_state))

        return {
            "session_restored": existing_session is not None,
            "files_tracking": len(self.initial_state),
        }

    def get_tracked_files(self) -> Dict[str, Dict]:
        """
        Get a list of all currently tracked files with their details.
        Now uses S3 filenames as IDs for consistency.
        """
        current_files = self.scan_directory()
        logger.info("Found %d files in working directory", len(current_files))

        # First get index for mappings and existing files with backward compatibility
        tracked_files = {}
        try:
            filename_mappings = {}
            version_history = {}
            
            # Try consolidation bucket first
            consolidation_index_key = f"agentState/{self.current_user}/{self.session_id}/index.json"
            try:
                response = self.s3_client.get_object(Bucket=self.consolidation_bucket, Key=consolidation_index_key)
                index_data = json.loads(response["Body"].read().decode("utf-8"))
                filename_mappings = index_data.get("mappings", {})
                version_history = index_data.get("version_history", {})
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey" and self.legacy_bucket:
                    # Fallback to legacy bucket
                    legacy_index_key = f"{self.current_user}/{self.session_id}/index.json"
                    try:
                        response = self.s3_client.get_object(Bucket=self.legacy_bucket, Key=legacy_index_key)
                        index_data = json.loads(response["Body"].read().decode("utf-8"))
                        filename_mappings = index_data.get("mappings", {})
                        version_history = index_data.get("version_history", {})
                    except ClientError:
                        filename_mappings = {}
                        version_history = {}
                else:
                    filename_mappings = {}
                    version_history = {}

            for filepath, info in current_files.items():
                # Get the S3 filename from mappings if it exists
                if filepath in filename_mappings:
                    s3_filename = filename_mappings[filepath]
                    # Remove extension to get base UUID
                    file_id = s3_filename.rsplit(".", 1)[0]
                else:
                    # Generate new UUID for new files
                    file_id = str(uuid.uuid4())

                tracked_files[file_id] = {
                    "original_name": filepath,
                    "size": info["size"],
                    "last_modified": datetime.fromtimestamp(info["mtime"]).isoformat(),
                    "s3_filename": filename_mappings.get(filepath),
                }

                # Add version history if available
                if filepath in version_history:
                    tracked_files[file_id]["versions"] = version_history[filepath]

        except Exception as e:
            logger.error("Error getting tracked files: %s", e)

        return tracked_files

    def _decode_image_if_base64(self, file_path: str, source_name: str) -> None:
        """
        Check if an image file contains base64 encoded data and decode it if necessary.

        Args:
            file_path: Path to the file that may contain base64 encoded data
            source_name: Name of the source for logging purposes
        """
        try:
            logger.debug("Checking if image needs base64 decoding: %s", source_name)
            # Read the file content
            with open(file_path, "rb") as f:
                content = f.read()

            # Skip if file is empty
            if not content:
                logger.debug("Empty file, skipping base64 check: %s", source_name)
                return

            # Check if content appears to be base64
            # First, try to decode as UTF-8 string
            try:
                content_str = content.decode("utf-8", errors="ignore")

                # Heuristics to detect base64:
                # 1. Starts with data: URI prefix
                # 2. Content is mostly printable ASCII characters (base64 alphabet)
                # 3. No binary data in first chunk of content
                is_printable = all(
                    c.isprintable() or c.isspace() for c in content_str[:1000]
                )
                logger.debug("Content is printable: %s", is_printable)
                has_data_uri = content_str.startswith("data:")
                logger.debug("Content has data URI prefix: %s", has_data_uri)
                looks_like_base64 = is_printable or (
                    has_data_uri or "," in content_str[:100]
                )
                logger.debug("Looks like base64: %s", looks_like_base64)

                if looks_like_base64:
                    import base64

                    logger.info(f"Decoding base64 image data for {source_name}")

                    # If it has the data:image prefix, extract just the base64 part
                    if has_data_uri:
                        header, base64_data = content_str.split(",", 1)
                    else:
                        # Assume the entire content is base64
                        base64_data = content_str.strip()

                    # Clean any whitespace or line breaks that might be in the base64 string
                    base64_data = "".join(base64_data.split())

                    # Decode and write back to the same file
                    try:
                        decoded_data = base64.b64decode(base64_data)
                        with open(file_path, "wb") as f:
                            f.write(decoded_data)
                        logger.info("Successfully decoded base64 image: %s", source_name)
                    except Exception as decode_error:
                        logger.error("Error decoding base64 data: %s", decode_error)
            except UnicodeDecodeError:
                # If we can't decode as UTF-8, it's likely already binary
                logger.debug(
                    "File appears to be binary already, no decoding needed: %s", source_name
                )

        except Exception as e:
            logger.error("Error checking/decoding image: %s", e)
            # We don't raise the exception - continue with original file if decoding fails

    def add_data_source(self, data_source: Dict) -> Dict:
        """
        Add a data source to the file tracker. This downloads the data source from S3
        or from a signed URL and makes it available in the working directory.

        Args:
            data_source: A data source object with id, name, type and optionally ref and format

        Returns:
            A dictionary with status and information about the data source
        """
        if not self.consolidation_bucket:
            raise ValueError("S3_CONSOLIDATION_BUCKET_NAME environment variable not set")

        # Extract information from the data source
        source_id = data_source.get("id")
        source_name = data_source.get("name")
        source_type = data_source.get("type")
        source_ref = data_source.get("ref")  # URL or content reference
        source_format = data_source.get("format")  # signedUrl or content

        logger.info(
            "Adding data source %s of type %s with name %s, format: %s", source_id, source_type, source_name, source_format
        )
        logger.debug("Full data source: %s", json.dumps(data_source, indent=2))

        if not source_id:
            logger.error("Data source missing required id field")
            return {
                "status": "error",
                "message": "Data source missing required id field",
                "data_source": data_source,
            }

        # Generate a filename if not provided
        if not source_name:
            source_name = source_id.split("/")[-1]
            if "." not in source_name:
                # Add extension based on type if needed
                if source_type == "application/json":
                    source_name += ".json"
                elif source_type and source_type.startswith("image/"):
                    ext = source_type.split("/")[-1]
                    source_name += f".{ext}"

        logger.debug("Using filename: %s", source_name)

        # Check if this data source has already been added
        if source_id in self.data_sources:
            local_path = os.path.join(self.working_dir, self.data_sources[source_id])
            if os.path.exists(local_path):
                logger.info("Data source %s already exists at %s", source_id, local_path)
                return {
                    "status": "success",
                    "message": "Data source already downloaded",
                    "local_path": self.data_sources[source_id],
                    "already_exists": True,
                }

        # Create a local file path using the source name
        local_path = os.path.join(self.working_dir, source_name)

        # Ensure the target directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        try:
            # Handling based on the format and reference
            if source_format == "signedUrl" and source_ref:
                logger.info("Downloading from signed URL: %s", source_ref)

                try:
                    response = requests.get(source_ref)
                    response.raise_for_status()

                    # Write the content to the local file
                    with open(local_path, "wb") as f:
                        f.write(response.content)

                    # If this is an image, check if it needs base64 decoding
                    if source_type and source_type.startswith("image/"):
                        self._decode_image_if_base64(local_path, source_name)

                    logger.info("Downloaded data source from signed URL to %s", local_path)
                except requests.exceptions.RequestException as e:
                    logger.error("Error downloading from signed URL: %s", e)
                    return {
                        "status": "error",
                        "message": f"Error downloading from signed URL: {str(e)}",
                        "data_source": data_source,
                    }

            elif source_format == "content" and source_ref:
                logger.info("Saving content directly to file")

                # Handle base64 encoded images
                if (
                    source_type
                    and source_type.startswith("image/")
                    and isinstance(source_ref, str)
                    and source_ref.startswith("data:")
                ):
                    logger.info("Decoding base64 image data for %s", source_name)
                    try:
                        # Extract the base64 content after the data URI prefix
                        import base64

                        # Format typically: data:image/png;base64,<actual-base64-data>
                        header, base64_data = source_ref.split(",", 1)
                        decoded_data = base64.b64decode(base64_data)

                        # Write decoded binary data to file
                        with open(local_path, "wb") as f:
                            f.write(decoded_data)
                    except Exception as e:
                        logger.error("Error decoding base64 image: %s", e)
                        return {
                            "status": "error",
                            "message": f"Error decoding base64 image: {str(e)}",
                            "data_source": data_source,
                        }
                # For JSON content, we may need to parse it first
                elif source_type == "application/json" and isinstance(source_ref, str):
                    try:
                        content = json.loads(source_ref)
                        with open(local_path, "w") as f:
                            json.dump(content, f, indent=2)
                    except json.JSONDecodeError:
                        # If not valid JSON, save as string
                        with open(local_path, "w") as f:
                            f.write(source_ref)
                else:
                    # For other content types, save directly
                    mode = "wb" if isinstance(source_ref, bytes) else "w"
                    with open(local_path, mode) as f:
                        f.write(source_ref)

                    # If this is an image, check if it needs base64 decoding
                    if source_type and source_type.startswith("image/") and mode == "w":
                        self._decode_image_if_base64(local_path, source_name)

                logger.info("Saved data source content to %s", local_path)

            # Legacy S3 download logic as fallback
            else:
                logger.info("Downloading data source from S3")
                # Parse the S3 URI to get bucket and key
                # Assuming format: s3://user/date/uuid.json
                if source_id.startswith("s3://"):
                    # Remove 's3://' prefix and split by '/'
                    parts = source_id[5:].split("/")
                    if len(parts) < 3:
                        logger.error("Invalid S3 URI format: %s", source_id)
                        return {
                            "status": "error",
                            "message": f"Invalid S3 URI format: {source_id}",
                            "data_source": data_source,
                        }

                    # The last part should be the object key
                    s3_key = parts[-1]
                    # The rest is the path in the bucket
                    if len(parts) > 3:  # If we have a nested path
                        s3_key = "/".join(parts[1:])
                else:
                    # If not an S3 URI, use as-is
                    s3_key = source_id

                logger.info("Downloading %s to %s", s3_key, local_path)

                try:
                    # Use consolidation bucket for new data source downloads
                    self.s3_client.download_file(self.consolidation_bucket, s3_key, local_path)
                    logger.info("Downloaded data source %s to %s", source_id, local_path)

                    # Convert base64 encoded images to binary if needed
                    if source_type and source_type.startswith("image/"):
                        self._decode_image_if_base64(local_path, source_name)
                except ClientError as e:
                    logger.error("Error downloading data source %s: %s", source_id, e)
                    return {
                        "status": "error",
                        "message": f"Error downloading data source: {str(e)}",
                        "data_source": data_source,
                    }

            # Store the mapping from source ID to local path
            self.data_sources[source_id] = source_name

            # Don't add to initial state so the data source will be detected as a changed file
            # and properly synced to S3

            return {
                "status": "success",
                "message": "Data source downloaded successfully",
                "local_path": source_name,
                "already_exists": False,
            }

        except Exception as e:
            logger.error("Unexpected error adding data source %s: %s", source_id, e)
            return {
                "status": "error",
                "message": f"Unexpected error: {str(e)}",
                "data_source": data_source,
            }

    def get_changed_files(
        self,
    ) -> Tuple[List[str], Dict[str, str], Dict[str, Dict], List[str]]:
        """Get list of changed/new files and their S3 mappings, with version info.
        Also returns list of deleted files that should be removed from S3 index."""
        current_state = self.scan_directory()
        changed_files = []
        deleted_files = []
        filename_mapping = self.existing_mappings.copy()
        version_info = {}

        # Check for modified or new files
        for filepath, current_info in current_state.items():
            if (
                filepath not in self.initial_state
                or current_info["hash"] != self.initial_state[filepath]["hash"]
            ):
                changed_files.append(filepath)
                new_s3_name = str(uuid.uuid4()) + Path(filepath).suffix
                filename_mapping[filepath] = new_s3_name
                version_info[filepath] = {
                    "s3_name": new_s3_name,
                    "timestamp": datetime.utcnow().isoformat(),
                    "hash": current_info["hash"],
                    "size": current_info["size"],
                }

        # Check for deleted files (exist in initial_state but not in current_state)
        for filepath in self.initial_state:
            if filepath not in current_state:
                deleted_files.append(filepath)
                # Remove the mapping for deleted files
                if filepath in filename_mapping:
                    filename_mapping.pop(filepath)

        return changed_files, filename_mapping, version_info, deleted_files

    def upload_changed_files(self) -> Dict:
        """Upload changed files to consolidation S3 bucket and return upload information."""
        if not self.consolidation_bucket:
            raise ValueError("S3_CONSOLIDATION_BUCKET_NAME environment variable not set")

        changed_files, filename_mapping, version_info, deleted_files = (
            self.get_changed_files()
        )

        if not changed_files and not deleted_files:
            return {
                "status": "success",
                "message": "No files changed or deleted",
                "files_processed": 0,
                "mappings": self.existing_mappings,
            }

        try:
            # Get existing index if it exists - always use consolidation bucket for new uploads
            consolidation_index_key = f"agentState/{self.current_user}/{self.session_id}/index.json"
            try:
                response = self.s3_client.get_object(Bucket=self.consolidation_bucket, Key=consolidation_index_key)
                existing_index = json.loads(response["Body"].read().decode("utf-8"))
                # Ensure version_history exists
                if "version_history" not in existing_index:
                    existing_index["version_history"] = {}
                if "deleted_files" not in existing_index:
                    existing_index["deleted_files"] = []
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    existing_index = {
                        "user": self.current_user,
                        "session_id": self.session_id,
                        "mappings": {},
                        "version_history": {},
                        "deleted_files": [],
                    }
                else:
                    raise

            # Update version history while maintaining backward compatibility
            for filepath, version_data in version_info.items():
                if filepath not in existing_index["version_history"]:
                    existing_index["version_history"][filepath] = []

                # Add new version
                existing_index["version_history"][filepath].append(version_data)

            # Replace mappings entirely to ensure deletions are reflected
            existing_index["mappings"] = filename_mapping
            existing_index["timestamp"] = datetime.utcnow().isoformat()

            # Add data sources mapping to the index file
            if not "data_sources" in existing_index:
                existing_index["data_sources"] = {}
            existing_index["data_sources"].update(self.data_sources)

            # Update deleted files list in the index
            if deleted_files:
                logger.info("Deleted files removed from mappings: %s", deleted_files)
                # Add new deleted files to the list, avoiding duplicates
                for df in deleted_files:
                    if df not in existing_index["deleted_files"]:
                        existing_index["deleted_files"].append(df)

                # Save deleted files to instance variable for next session
                self.deleted_files = existing_index["deleted_files"]

            # Upload index file to consolidation bucket
            self.s3_client.put_object(
                Bucket=self.consolidation_bucket,
                Key=consolidation_index_key,
                Body=json.dumps(existing_index, indent=2),
                ContentType="application/json",
            )

            # Upload changed files to consolidation bucket with agentState/ prefix
            upload_results = {}
            for original_path in changed_files:
                safe_name = filename_mapping[original_path]
                s3_key = f"agentState/{self.current_user}/{self.session_id}/{safe_name}"
                local_path = os.path.join(self.working_dir, original_path)

                try:
                    with open(local_path, "rb") as file:
                        self.s3_client.upload_fileobj(file, self.consolidation_bucket, s3_key)
                    upload_results[original_path] = {
                        "status": "success",
                        "s3_key": s3_key,
                    }
                except Exception as e:
                    upload_results[original_path] = {"status": "error", "error": str(e)}

            return {
                "status": "success",
                "message": f"Processed {len(changed_files)} files, removed {len(deleted_files)} deleted files",
                "files_processed": len(changed_files),
                "files_deleted": len(deleted_files),
                "changed_files": changed_files,
                "deleted_files": deleted_files,
                "mappings": filename_mapping,
                "upload_results": upload_results,
                "index_location": {"bucket": self.consolidation_bucket, "key": consolidation_index_key},
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "files_processed": 0,
                "mappings": {},
            }


def create_file_tracker(
    current_user: str, session_id: str, working_dir: str
) -> LambdaFileTracker:
    """
    Create and initialize a LambdaFileTracker instance.

    Usage:
        # At the start of your Lambda function
        tracker = create_file_tracker(current_user, session_id)

        # Write files to the tracker
        tracker.write_file('example.txt', 'Hello, World!')

        # At the end of your function
        results = tracker.upload_changed_files()
    """
    logger.info(
        "Creating file tracker for %s/%s with working directory %s", current_user, session_id, working_dir
    )

    tracker = LambdaFileTracker(current_user, session_id, working_dir)
    tracker.start_tracking()
    return tracker


def get_file_versions(self, filepath: str) -> Optional[List[Dict]]:
    """Get version history for a specific file."""
    try:
        index_key = f"{self.current_user}/{self.session_id}/index.json"
        response = self.s3_client.get_object(Bucket=self.bucket, Key=index_key)
        index_data = json.loads(response["Body"].read().decode("utf-8"))
        return index_data.get("version_history", {}).get(filepath, [])
    except Exception as e:
        logger.error("Error retrieving file versions: %s", e)
        return None


def get_presigned_url_by_id(
    current_user: str, session_id: str, file_id: str, expiration: int = 3600
) -> Optional[str]:
    """
    Generate a presigned URL for a file version with backward compatibility for consolidation and legacy buckets.

    Args:
        current_user (str): The user ID
        session_id (str): The session ID containing the file
        file_id (str): The UUID string representing either the current file or a specific version
    """
    s3_client = boto3.client("s3")
    consolidation_bucket = os.getenv("S3_CONSOLIDATION_BUCKET_NAME")
    legacy_bucket = os.getenv("AGENT_STATE_BUCKET")  # Marked for deletion

    if not consolidation_bucket:
        raise ValueError("S3_CONSOLIDATION_BUCKET_NAME environment variable not set")

    # Try consolidation bucket first (migrated records)
    consolidation_index_key = f"agentState/{current_user}/{session_id}/index.json"
    try:
        response = s3_client.get_object(Bucket=consolidation_bucket, Key=consolidation_index_key)
        index_content = json.loads(response["Body"].read().decode("utf-8"))

        # Found in consolidation bucket - use agentState/ prefix
        bucket_to_use = consolidation_bucket
        key_prefix = f"agentState/{current_user}/{session_id}/"
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey" and legacy_bucket:
            # Fallback to legacy bucket
            legacy_index_key = f"{current_user}/{session_id}/index.json"
            try:
                response = s3_client.get_object(Bucket=legacy_bucket, Key=legacy_index_key)
                index_content = json.loads(response["Body"].read().decode("utf-8"))
                bucket_to_use = legacy_bucket
                key_prefix = f"{current_user}/{session_id}/"
            except ClientError:
                logger.warning("File index not found in either bucket for session %s", session_id)
                return None
        else:
            logger.error("Error accessing consolidation bucket index: %s", e)
            return None
    
    # Search for file in mappings and version history
    filename_mappings = index_content.get("mappings", {})
    s3_key = None
    
    # First check current mappings
    for _, s3_filename in filename_mappings.items():
        if s3_filename.rsplit(".", 1)[0] == file_id:
            s3_key = f"{key_prefix}{s3_filename}"
            break
    
    # If not found in current mappings, check version history
    if not s3_key:
        version_history = index_content.get("version_history", {})
        for _, versions in version_history.items():
            for version in versions:
                if version.get("s3_name", "").rsplit(".", 1)[0] == file_id:
                    s3_key = f"{key_prefix}{version['s3_name']}"
                    break
            if s3_key:
                break

    if not s3_key:
        logger.warning("File ID %s not found in session mappings or version history", file_id)
        return None

    try:
        # Generate the presigned URL
        url = s3_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket_to_use, "Key": s3_key}, ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        logger.error("Error generating presigned URL: %s", e)
        return None
