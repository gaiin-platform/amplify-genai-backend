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

class LambdaFileTracker:
    def __init__(self, current_user: str, session_id: str, working_dir: str = "/tmp"):
        self.current_user = current_user
        self.session_id = session_id
        self.existing_mappings: Dict[str, str] = {}
        self.initial_state: Dict[str, Dict] = {}
        self.s3_client = boto3.client('s3')
        self.bucket = os.getenv('AGENT_STATE_BUCKET')

        # Create session working directory in /tmp
        self.working_dir = working_dir
        os.makedirs(self.working_dir, exist_ok=True)

    def __del__(self):
        """Cleanup temporary files when object is destroyed."""
        try:
            if os.path.exists(self.working_dir):
                shutil.rmtree(self.working_dir)
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")

    def get_file_info(self, filepath: str) -> Dict:
        """Get file size and hash for a given file."""
        stats = os.stat(filepath)
        with open(filepath, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        return {
            'size': stats.st_size,
            'hash': file_hash,
            'mtime': stats.st_mtime
        }

    def scan_directory(self) -> Dict[str, Dict]:
        """Scan working directory and return file information."""
        file_info = {}
        for root, _, files in os.walk(self.working_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.working_dir)
                try:
                    print(f"Found file {rel_path}")
                    file_info[rel_path] = self.get_file_info(full_path)
                except (IOError, OSError) as e:
                    print(f"Error reading file {full_path}: {e}")
        return file_info

    def find_existing_session(self) -> Optional[Dict]:
        """Look for existing session files in S3."""
        try:
            # Look for index.json directly under user/session_id
            index_key = f"{self.current_user}/{self.session_id}/index.json"
            try:
                response = self.s3_client.get_object(
                    Bucket=self.bucket,
                    Key=index_key
                )
                session_data = json.loads(response['Body'].read().decode('utf-8'))
                # Store existing mappings when session is found
                self.existing_mappings = session_data.get('mappings', {})
                return session_data

            except ClientError as e:
                print(f"Error checking for existing session: {e}")
                if e.response['Error']['Code'] == 'NoSuchKey':
                    return None
                raise

        except Exception as e:
            print(f"Error checking for existing session: {e}")
            return None

    def restore_session_files(self, index_content: Dict) -> bool:
        """Restore files from a previous session to the working directory."""
        try:
            # Download each file from the index
            for original_path, s3_name in index_content['mappings'].items():
                # Construct the S3 key without date
                s3_key = f"{self.current_user}/{self.session_id}/{s3_name}"
                print(f"Restoring {original_path} from {s3_key}")
                local_path = os.path.join(self.working_dir, original_path)

                # Ensure the target directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                try:
                    # Download the file
                    self.s3_client.download_file(
                        self.bucket,
                        s3_key,
                        local_path
                    )
                except ClientError as e:
                    print(f"Error downloading file {s3_key}: {e}")
                    continue

            return True

        except Exception as e:
            print(f"Error restoring session files: {e}")
            return False

    def write_file(self, filename: str, content: bytes | str) -> str:
        """
        Write content to a file in the working directory.
        Returns the relative path to the file.
        """
        local_path = os.path.join(self.working_dir, filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        mode = 'wb' if isinstance(content, bytes) else 'w'
        with open(local_path, mode) as f:
            f.write(content)

        return filename

    def read_file(self, filename: str) -> bytes:
        """Read content from a file in the working directory."""
        local_path = os.path.join(self.working_dir, filename)
        with open(local_path, 'rb') as f:
            return f.read()

    def start_tracking(self) -> Dict:
        """Start tracking files, optionally restoring from a previous session."""
        print("Checking for existing session...")

        existing_session = self.find_existing_session()

        if existing_session:
            print("Found existing session:")

            # Print the full existing session details
            print(json.dumps(existing_session, indent=2))

            if self.restore_session_files(existing_session):
                print("Successfully restored session files")
            else:
                print("Failed to restore some session files")
        else:
            print("No existing session found, starting fresh tracking")

        # Start tracking current state
        self.initial_state = self.scan_directory()
        print(f"Now tracking {len(self.initial_state)} files")

        return {
            "session_restored": existing_session is not None,
            "files_tracking": len(self.initial_state)
        }

    def get_tracked_files(self) -> Dict[str, Dict]:
        """
        Get a list of all currently tracked files with their details.
        Now uses S3 filenames as IDs for consistency.
        """
        current_files = self.scan_directory()
        print(f"Found {len(current_files)} files in working directory")

        # First get index for mappings and existing files
        tracked_files = {}
        try:
            index_key = f"{self.current_user}/{self.session_id}/index.json"
            try:
                response = self.s3_client.get_object(
                    Bucket=self.bucket,
                    Key=index_key
                )
                index_data = json.loads(response['Body'].read().decode('utf-8'))
                filename_mappings = index_data.get('mappings', {})
                version_history = index_data.get('version_history', {})
            except ClientError:
                filename_mappings = {}
                version_history = {}

            for filepath, info in current_files.items():
                # Get the S3 filename from mappings if it exists
                if filepath in filename_mappings:
                    s3_filename = filename_mappings[filepath]
                    # Remove extension to get base UUID
                    file_id = s3_filename.rsplit('.', 1)[0]
                else:
                    # Generate new UUID for new files
                    file_id = str(uuid.uuid4())

                tracked_files[file_id] = {
                    "original_name": filepath,
                    "size": info['size'],
                    "last_modified": datetime.fromtimestamp(info['mtime']).isoformat(),
                    "s3_filename": filename_mappings.get(filepath)
                }

                # Add version history if available
                if filepath in version_history:
                    tracked_files[file_id]["versions"] = version_history[filepath]

        except Exception as e:
            print(f"Error getting tracked files: {e}")

        return tracked_files

    def get_changed_files(self) -> Tuple[List[str], Dict[str, str], Dict[str, Dict]]:
        """Get list of changed/new files and their S3 mappings, with version info."""
        current_state = self.scan_directory()
        changed_files = []
        filename_mapping = self.existing_mappings.copy()
        version_info = {}

        # Check for modified or new files
        for filepath, current_info in current_state.items():
            if filepath not in self.initial_state or \
                    current_info['hash'] != self.initial_state[filepath]['hash']:
                changed_files.append(filepath)
                new_s3_name = str(uuid.uuid4()) + Path(filepath).suffix
                filename_mapping[filepath] = new_s3_name
                version_info[filepath] = {
                    's3_name': new_s3_name,
                    'timestamp': datetime.utcnow().isoformat(),
                    'hash': current_info['hash'],
                    'size': current_info['size']
                }

        return changed_files, filename_mapping, version_info

    def upload_changed_files(self) -> Dict:
        """Upload changed files to S3 and return upload information."""
        if not self.bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        changed_files, filename_mapping, version_info = self.get_changed_files()

        if not changed_files:
            return {
                "status": "success",
                "message": "No files changed",
                "files_processed": 0,
                "mappings": self.existing_mappings
            }

        try:
            # Get existing index if it exists
            try:
                index_key = f"{self.current_user}/{self.session_id}/index.json"
                response = self.s3_client.get_object(
                    Bucket=self.bucket,
                    Key=index_key
                )
                existing_index = json.loads(response['Body'].read().decode('utf-8'))
                # Ensure version_history exists
                if 'version_history' not in existing_index:
                    existing_index['version_history'] = {}
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    existing_index = {
                        "user": self.current_user,
                        "session_id": self.session_id,
                        "mappings": {},
                        "version_history": {}
                    }
                else:
                    raise

            # Update version history while maintaining backward compatibility
            for filepath, version_data in version_info.items():
                if filepath not in existing_index['version_history']:
                    existing_index['version_history'][filepath] = []

                # Add new version
                existing_index['version_history'][filepath].append(version_data)

            # Update current mappings
            existing_index['mappings'].update(filename_mapping)
            existing_index['timestamp'] = datetime.utcnow().isoformat()

            # Upload index file
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=index_key,
                Body=json.dumps(existing_index, indent=2),
                ContentType='application/json'
            )

            # Upload changed files
            upload_results = {}
            for original_path in changed_files:
                safe_name = filename_mapping[original_path]
                s3_key = f"{self.current_user}/{self.session_id}/{safe_name}"
                local_path = os.path.join(self.working_dir, original_path)

                try:
                    with open(local_path, 'rb') as file:
                        self.s3_client.upload_fileobj(file, self.bucket, s3_key)
                    upload_results[original_path] = {
                        "status": "success",
                        "s3_key": s3_key
                    }
                except Exception as e:
                    upload_results[original_path] = {
                        "status": "error",
                        "error": str(e)
                    }

            return {
                "status": "success",
                "message": f"Processed {len(changed_files)} files",
                "files_processed": len(changed_files),
                "changed_files": changed_files,
                "mappings": filename_mapping,
                "upload_results": upload_results,
                "index_location": {
                    "bucket": self.bucket,
                    "key": index_key
                }
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "files_processed": 0,
                "mappings": {}
            }

def create_file_tracker(current_user: str, session_id: str, working_dir: str) -> LambdaFileTracker:
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
    print(f"Creating file tracker for {current_user}/{session_id} with working directory {working_dir}")

    tracker = LambdaFileTracker(current_user, session_id, working_dir)
    tracker.start_tracking()
    return tracker


def get_file_versions(self, filepath: str) -> Optional[List[Dict]]:
    """Get version history for a specific file."""
    try:
        index_key = f"{self.current_user}/{self.session_id}/index.json"
        response = self.s3_client.get_object(
            Bucket=self.bucket,
            Key=index_key
        )
        index_data = json.loads(response['Body'].read().decode('utf-8'))
        return index_data.get('version_history', {}).get(filepath, [])
    except Exception as e:
        print(f"Error retrieving file versions: {e}")
        return None

def get_presigned_url_by_id(current_user: str, session_id: str, file_id: str, expiration: int = 3600) -> Optional[str]:
    """
    Generate a presigned URL for a file version in the agent state bucket.

    Args:
        current_user (str): The user ID
        session_id (str): The session ID containing the file
        file_id (str): The UUID string representing either the current file or a specific version
    """
    s3_client = boto3.client('s3')
    bucket = os.getenv('AGENT_STATE_BUCKET')

    if not bucket:
        raise ValueError("AGENT_STATE_BUCKET environment variable not set")

    index_key = f"{current_user}/{session_id}/index.json"
    try:
        response = s3_client.get_object(
            Bucket=bucket,
            Key=index_key
        )
        index_content = json.loads(response['Body'].read().decode('utf-8'))

        # First check current mappings
        filename_mappings = index_content.get('mappings', {})
        for _, s3_filename in filename_mappings.items():
            if s3_filename.rsplit('.', 1)[0] == file_id:
                s3_key = f"{current_user}/{session_id}/{s3_filename}"
                break
        else:
            # If not found in current mappings, check version history
            version_history = index_content.get('version_history', {})
            s3_key = None
            for _, versions in version_history.items():
                for version in versions:
                    if version.get('s3_name', '').rsplit('.', 1)[0] == file_id:
                        s3_key = f"{current_user}/{session_id}/{version['s3_name']}"
                        break
                if s3_key:
                    break

            if not s3_key:
                print(f"File ID {file_id} not found in session mappings or version history")
                return None

        # Generate the presigned URL
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': s3_key
            },
            ExpiresIn=expiration
        )
        return url

    except ClientError as e:
        print(f"Error generating presigned URL: {e}")
        return None