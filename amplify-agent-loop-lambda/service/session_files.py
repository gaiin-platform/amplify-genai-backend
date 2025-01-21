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
    def __init__(self, current_user: str, session_id: str):
        self.current_user = current_user
        self.session_id = session_id
        self.existing_mappings: Dict[str, str] = {}
        self.initial_state: Dict[str, Dict] = {}
        self.s3_client = boto3.client('s3')
        self.bucket = os.getenv('AGENT_STATE_BUCKET')

        # Create session working directory in /tmp
        self.working_dir = f"/tmp/{self.session_id}"
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
        Returns a dictionary mapping file IDs to their details.
        """
        current_files = self.scan_directory()
        tracked_files = {}

        for filepath, info in current_files.items():
            file_id = str(uuid.uuid4())  # Generate a unique ID for each file
            tracked_files[file_id] = {
                "original_name": filepath,
                "size": info['size'],
                "last_modified": datetime.fromtimestamp(info['mtime']).isoformat()
            }

        return tracked_files

    def get_changed_files(self) -> Tuple[List[str], Dict[str, str]]:
        """Get list of changed/new files and their S3 mappings."""
        current_state = self.scan_directory()
        changed_files = []
        filename_mapping = self.existing_mappings.copy()

        # Check for modified or new files
        for filepath, current_info in current_state.items():
            if filepath not in self.initial_state or \
                    current_info['hash'] != self.initial_state[filepath]['hash']:
                changed_files.append(filepath)
                filename_mapping[filepath] = str(uuid.uuid4()) + Path(filepath).suffix

        return changed_files, filename_mapping

    def upload_changed_files(self) -> Dict:
        """Upload changed files to S3 and return upload information."""
        if not self.bucket:
            raise ValueError("AGENT_STATE_BUCKET environment variable not set")

        changed_files, filename_mapping = self.get_changed_files()

        if not changed_files:
            return {
                "status": "success",
                "message": "No files changed",
                "files_processed": 0,
                "mappings": self.existing_mappings
            }

        upload_results = {}

        try:
            # Create and upload index file
            index_content = {
                "user": self.current_user,
                "session_id": self.session_id,
                "mappings": filename_mapping,
                "timestamp": datetime.utcnow().isoformat()
            }

            index_key = f"{self.current_user}/{self.session_id}/index.json"
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=index_key,
                Body=json.dumps(index_content, indent=2),
                ContentType='application/json'
            )

            # Upload changed files
            for original_path in changed_files:
                safe_name = filename_mapping[original_path]
                s3_key = f"{self.current_user}/{self.session_id}/{safe_name}"
                local_path = os.path.join(self.working_dir, original_path)

                try:
                    with open(local_path, 'rb') as file:
                        self.s3_client.upload_fileobj(
                            file,
                            self.bucket,
                            s3_key
                        )
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
                'changed_files': changed_files,
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

def create_file_tracker(current_user: str, session_id: str) -> LambdaFileTracker:
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
    tracker = LambdaFileTracker(current_user, session_id)
    tracker.start_tracking()
    return tracker