import re
import os
import shutil
import json
import zipfile
from typing import Dict, List, Optional, Union, Any

from agent.components.tool import register_tool


@register_tool(tags=["file_handling"])
def get_current_directory(action_context):
    """
    Returns the current working directory for the agent.

    This tool retrieves the working directory from the action context or
    falls back to the current system directory if not set.

    Args:
        action_context: The context containing agent environment information

    Returns:
        String path of the current working directory

    Examples:
        >>> get_current_directory(action_context)
        '/home/user/workspace/project'
    """
    wd = action_context.get("work_directory")
    if not wd:
        wd = os.getcwd()
    return wd


@register_tool(tags=["file_handling"])
def get_writeable_directory(action_context):
    """
    Returns a directory where the agent has write permissions.

    Similar to get_current_directory, this provides a path where files can be
    safely created or modified by the agent.

    Args:
        action_context: The context containing agent environment information

    Returns:
        String path to a writeable directory

    Examples:
        >>> get_writeable_directory(action_context)
        '/home/user/workspace/project/temp'
    """
    wd = action_context.get("work_directory")
    if not wd:
        wd = os.getcwd()
    return wd


@register_tool(tags=["file_handling"])
def list_files_in_directory(directory: str):
    """
    Lists all files and directories in the specified directory.

    Provides a simple listing of all entries in a directory without any filtering.
    For more advanced directory scanning, use the search_files function.

    Args:
        directory: Path to the directory to list

    Returns:
        List of filenames and directory names in the specified directory

    Examples:
        >>> list_files_in_directory('/home/user/documents')
        ['file1.txt', 'file2.pdf', 'images', 'notes.md']

        >>> list_files_in_directory('/var/empty')
        []
    """
    return os.listdir(directory)


# --- New file handling utilities ---


@register_tool(tags=["file_handling"])
def write_file_from_string(file_path: str, content: str, mode: str = "w"):
    """
    Writes string content to a file.

    Creates a new file or overwrites an existing file with the specified content.

    Args:
        file_path: Path where the file should be written
        content: String content to write to the file
        mode: File open mode ('w' for write/overwrite, 'a' for append)

    Returns:
        Confirmation message of successful write

    Examples:
        >>> write_file_from_string('/home/user/notes.txt', 'Hello, world!')
        'Content written to /home/user/notes.txt'

        >>> write_file_from_string('/home/user/log.txt', 'New entry', 'a')
        'Content written to /home/user/log.txt'
    """
    with open(file_path, mode, encoding="utf-8") as f:
        f.write(content)
    return f"Content written to {file_path}"


@register_tool(tags=["file_handling"])
def read_file(file_path: str):
    """
    Reads the entire content of a file as a string.

    Opens and reads a text file, returning its complete contents.
    For large files, consider using read_file_partial instead.

    Args:
        file_path: Path to the file to read

    Returns:
        Complete string content of the file

    Examples:
        >>> read_file('/home/user/notes.txt')
        'This is the content of my notes file.\nIt has multiple lines.\n'

        >>> read_file('/home/user/config.json')
        '{"setting1": true, "setting2": "value"}'
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@register_tool(tags=["file_handling"])
def read_file_partial(file_path: str, length: int, start: int = 0):
    """
    Reads a portion of a file from a starting position.

    Useful for reading specific sections of large files without loading
    the entire file into memory.

    Args:
        file_path: Path to the file to read
        length: Maximum number of characters to read
        start: Character position to start reading from (0-indexed)

    Returns:
        Substring of the file content

    Examples:
        >>> read_file_partial('/home/user/large_log.txt', 100, 0)
        'First 100 characters of the log file...'

        >>> read_file_partial('/home/user/large_log.txt', 50, 200)
        '50 characters starting from position 200...'
    """
    with open(file_path, "r", encoding="utf-8") as f:
        f.seek(start)
        return f.read(length)


@register_tool(tags=["file_handling"])
def search_files_recursive(search_root: str, pattern: str, use_regex: bool = False):
    """
    Searches for a string or regex pattern in all files under a directory recursively.

    This tool performs content-based search across multiple files, returning paths to
    files that contain matching content.

    Args:
        search_root: Directory to start searching from
        pattern: String or regex pattern to search for
        use_regex: Whether to interpret pattern as a regular expression

    Returns:
        List of file paths containing the pattern

    Examples:
        >>> search_files_recursive('/home/user/project', 'TODO')
        ['/home/user/project/src/main.py', '/home/user/project/docs/notes.md']

        >>> search_files_recursive('/home/user/project', 'function\\s+\\w+\\(', use_regex=True)
        ['/home/user/project/src/utils.py', '/home/user/project/src/main.py']
    """
    matches = []
    for root, _, files in os.walk(search_root):
        for filename in files:
            file_path = os.path.join(root, filename)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if (use_regex and re.search(pattern, content)) or (
                        not use_regex and pattern in content
                    ):
                        matches.append(file_path)
            except Exception as e:
                continue  # skip unreadable files
    return matches


@register_tool(tags=["file_handling"])
def get_directory_structure(path: str):
    """
    Builds a hierarchical representation of the directory structure.

    Creates a JSON-serializable object representing the directory tree,
    including only directories (not files).

    Args:
        path: Root directory path to start from

    Returns:
        Dictionary containing the directory structure with the following format:
        {
            "name": "directory_name",
            "path": "/full/path/to/directory",
            "type": "directory",
            "children": [
                {recursive directory objects}
            ]
        }

    Examples:
        >>> get_directory_structure('/home/user/project')
        {
            "name": "project",
            "path": "/home/user/project",
            "type": "directory",
            "children": [
                {
                    "name": "src",
                    "path": "/home/user/project/src",
                    "type": "directory",
                    "children": []
                },
                {
                    "name": "docs",
                    "path": "/home/user/project/docs",
                    "type": "directory",
                    "children": []
                }
            ]
        }
    """

    def build_tree(current_path):
        tree = {
            "name": os.path.basename(current_path),
            "path": current_path,
            "type": "directory",
        }
        tree["children"] = []
        for entry in os.listdir(current_path):
            full_path = os.path.join(current_path, entry)
            if os.path.isdir(full_path):
                tree["children"].append(build_tree(full_path))
        return tree

    return build_tree(path)


# --- Additional File Handling Tools ---


@register_tool(tags=["file_handling"])
def copy_file(source_path: str, destination_path: str, overwrite: bool = False):
    """
    Copies a file from source to destination with smart path handling.

    This tool safely copies files between locations, with options to control
    overwrite behavior and automatic directory creation.

    Args:
        source_path: Path to the source file
        destination_path: Path where the file should be copied to
        overwrite: Whether to overwrite existing files (defaults to False)

    Returns:
        Success or error message string

    Examples:
        >>> copy_file('/home/user/documents/report.pdf', '/home/user/backup/report.pdf')
        "File copied from '/home/user/documents/report.pdf' to '/home/user/backup/report.pdf'"

        >>> copy_file('/home/user/config.json', '/home/user/backup/config.json', overwrite=True)
        "File copied from '/home/user/config.json' to '/home/user/backup/config.json'"

        >>> copy_file('/home/user/missing.txt', '/home/user/backup/file.txt')
        "Error: Source file '/home/user/missing.txt' does not exist"

        >>> copy_file('/home/user/config.json', '/home/user/backup/nested/config.json')
        "File copied from '/home/user/config.json' to '/home/user/backup/nested/config.json'"
        # Note: This automatically creates the 'nested' directory if it doesn't exist

    Notes:
        - If the destination directory doesn't exist, it will be created automatically
        - Uses shutil.copy2 which preserves file metadata (timestamp, permissions)
        - Returns clear error messages for common failure scenarios
    """
    if not os.path.exists(source_path):
        return f"Error: Source file '{source_path}' does not exist"

    if os.path.exists(destination_path) and not overwrite:
        return f"Error: Destination file '{destination_path}' already exists and overwrite is set to False"

    try:
        # Create destination directory if it doesn't exist
        dest_dir = os.path.dirname(destination_path)
        if dest_dir and not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        shutil.copy2(source_path, destination_path)
        return f"File copied from '{source_path}' to '{destination_path}'"
    except Exception as e:
        return f"Error copying file: {str(e)}"


@register_tool(tags=["file_handling"])
def move_file(source_path: str, destination_path: str, overwrite: bool = False):
    """
    Moves (relocates) a file from source to destination.

    This tool combines the functionality of copy and delete, relocating a file
    to a new path and removing the original. It handles directory creation
    and provides options for overwrite control.

    Args:
        source_path: Path to the source file to be moved
        destination_path: Path where the file should be moved to
        overwrite: Whether to overwrite destination if it exists (defaults to False)

    Returns:
        Success or error message string

    Examples:
        >>> move_file('/home/user/downloads/data.csv', '/home/user/project/data/data.csv')
        "File moved from '/home/user/downloads/data.csv' to '/home/user/project/data/data.csv'"

        >>> move_file('/home/user/temp.txt', '/home/user/docs/notes.txt', overwrite=True)
        "File moved from '/home/user/temp.txt' to '/home/user/docs/notes.txt'"

        >>> move_file('/home/user/nonexistent.txt', '/home/user/backup/file.txt')
        "Error: Source file '/home/user/nonexistent.txt' does not exist"

        >>> move_file('/home/user/important.txt', '/home/user/backup/important.txt')
        "Error: Destination file '/home/user/backup/important.txt' already exists and overwrite is set to False"

    Notes:
        - If the destination directory doesn't exist, it will be created automatically
        - Uses shutil.move which attempts to use rename operations for efficiency when possible
        - On Unix, if source and destination are on the same filesystem, this is atomic
        - Returns clear error messages for common failure scenarios
    """
    if not os.path.exists(source_path):
        return f"Error: Source file '{source_path}' does not exist"

    if os.path.exists(destination_path) and not overwrite:
        return f"Error: Destination file '{destination_path}' already exists and overwrite is set to False"

    try:
        # Create destination directory if it doesn't exist
        dest_dir = os.path.dirname(destination_path)
        if dest_dir and not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        shutil.move(source_path, destination_path)
        return f"File moved from '{source_path}' to '{destination_path}'"
    except Exception as e:
        return f"Error moving file: {str(e)}"


@register_tool(tags=["file_handling"])
def rename_file(file_path: str, new_name: str, overwrite: bool = False):
    """
    Renames a file, keeping it in the same directory.

    This tool changes only the name of a file, not its location. The file
    remains in the same directory, but with a new filename.

    Args:
        file_path: Path to the file to rename
        new_name: New name for the file (not a path, just the filename)
        overwrite: Whether to overwrite existing files with the same name (defaults to False)

    Returns:
        Success or error message string

    Examples:
        >>> rename_file('/home/user/documents/old_report.pdf', 'annual_report_2023.pdf')
        "File '/home/user/documents/old_report.pdf' renamed to '/home/user/documents/annual_report_2023.pdf'"

        >>> rename_file('/home/user/temp/data.json', 'settings.json', overwrite=True)
        "File '/home/user/temp/data.json' renamed to '/home/user/temp/settings.json'"

        >>> rename_file('/home/user/missing.txt', 'found.txt')
        "Error: File '/home/user/missing.txt' does not exist"

        >>> rename_file('/home/user/important.txt', 'backup.txt')
        "Error: File with name 'backup.txt' already exists and overwrite is set to False"

    Notes:
        - This only changes the name, not the location; to move a file, use move_file
        - The new_name should be just the filename, not a path
        - Uses os.rename which is fast and atomic on most platforms
        - Returns clear error messages for common failure scenarios
    """
    if not os.path.exists(file_path):
        return f"Error: File '{file_path}' does not exist"

    directory = os.path.dirname(file_path)
    new_path = os.path.join(directory, new_name)

    if os.path.exists(new_path) and not overwrite:
        return f"Error: File with name '{new_name}' already exists and overwrite is set to False"

    try:
        os.rename(file_path, new_path)
        return f"File '{file_path}' renamed to '{new_path}'"
    except Exception as e:
        return f"Error renaming file: {str(e)}"


@register_tool(tags=["file_handling"])
def delete_file(file_path: str, force: bool = False):
    """
    Deletes a file from the filesystem.

    This tool permanently removes a file. It includes safety options like force
    to control behavior when the target file doesn't exist.

    Args:
        file_path: Path to the file to delete
        force: Whether to ignore errors if the file doesn't exist (defaults to False)

    Returns:
        Success or error message string

    Examples:
        >>> delete_file('/home/user/documents/old_draft.txt')
        "File '/home/user/documents/old_draft.txt' deleted successfully"

        >>> delete_file('/home/user/temp/cache.dat', force=True)
        "File '/home/user/temp/cache.dat' deleted successfully"

        >>> delete_file('/home/user/nonexistent.txt')
        "Error: File '/home/user/nonexistent.txt' does not exist"

        >>> delete_file('/home/user/nonexistent.txt', force=True)
        "File '/home/user/nonexistent.txt' doesn't exist, no action taken"

    Notes:
        - This permanently deletes the file, it does not move it to trash/recycle bin
        - The force parameter allows graceful handling of missing files
        - Will fail if the file exists but permission is denied
        - Not suitable for directories (use shutil.rmtree for that)
        - Returns clear error messages for common failure scenarios
    """
    if not os.path.exists(file_path):
        if force:
            return f"File '{file_path}' doesn't exist, no action taken"
        else:
            return f"Error: File '{file_path}' does not exist"

    try:
        os.remove(file_path)
        return f"File '{file_path}' deleted successfully"
    except Exception as e:
        return f"Error deleting file: {str(e)}"


@register_tool(tags=["file_handling"])
def zip_files(
    output_zip_path: str, file_mapping: Dict[str, str] = None, directory: str = None
):
    """
    Creates a zip archive with flexible file organization options.

    This powerful compression tool offers two modes of operation:
    1. Custom file mapping - specify exactly which files go where in the zip
    2. Directory archiving - compress an entire directory with its structure

    Args:
        output_zip_path: Path where the zip file should be created
        file_mapping: Dictionary mapping source file paths to destination paths within the zip
                      Example: {"path/to/source/file.txt": "folder/in/zip/file.txt"}
        directory: Optional directory to zip entirely (alternative to file_mapping)

    Returns:
        Success or error message string

    Examples:
        >>> # Example 1: Create a zip with specific file organization
        >>> mapping = {
        ...     "/home/user/documents/report.pdf": "reports/annual.pdf",
        ...     "/home/user/images/logo.png": "assets/branding/logo.png",
        ...     "/home/user/data.csv": "data.csv"
        ... }
        >>> zip_files('/home/user/archive.zip', file_mapping=mapping)
        "Zip file created at '/home/user/archive.zip'"

        >>> # Example 2: Compress an entire directory with structure
        >>> zip_files('/home/user/project_backup.zip', directory='/home/user/project')
        "Zip file created at '/home/user/project_backup.zip'"

        >>> # Example 3: Error handling for missing sources
        >>> zip_files('/home/user/test.zip', file_mapping={"/nonexistent/file.txt": "file.txt"})
        "Error: Source file '/nonexistent/file.txt' does not exist"

        >>> # Example 4: Error when no input is provided
        >>> zip_files('/home/user/empty.zip')
        "Error: Either file_mapping or directory must be provided"

    Notes:
        - You must provide either file_mapping or directory (or both)
        - The parent directory for the output zip will be created if it doesn't exist
        - Uses ZIP_DEFLATED compression for good compression ratios
        - When using directory mode, paths inside the zip are relative to the parent of the directory
        - Returns detailed error messages for troubleshooting
    """
    if file_mapping is None and directory is None:
        return "Error: Either file_mapping or directory must be provided"

    try:
        # Create parent directory for zip file if it doesn't exist
        zip_dir = os.path.dirname(output_zip_path)
        if zip_dir and not os.path.exists(zip_dir):
            os.makedirs(zip_dir)

        with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Add files from mapping
            if file_mapping:
                for src_path, dest_path in file_mapping.items():
                    if os.path.exists(src_path):
                        zipf.write(src_path, dest_path)
                    else:
                        return f"Error: Source file '{src_path}' does not exist"

            # Add entire directory
            if directory:
                if not os.path.exists(directory):
                    return f"Error: Directory '{directory}' does not exist"

                for root, _, files in os.walk(directory):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Calculate path inside zip file (relative to the directory)
                        arc_name = os.path.relpath(
                            file_path, os.path.dirname(directory)
                        )
                        zipf.write(file_path, arc_name)

        return f"Zip file created at '{output_zip_path}'"
    except Exception as e:
        return f"Error creating zip file: {str(e)}"


@register_tool(tags=["file_handling"])
def unzip_files(
    zip_path: str, extract_dir: str = None, extract_pattern: Optional[List[str]] = None
):
    """
    Extracts files from a zip archive with powerful filtering options.

    This extraction tool provides flexibility in how and where zip contents are
    extracted, with pattern-based filtering to extract only specific files.

    Args:
        zip_path: Path to the zip file to extract
        extract_dir: Directory to extract files to (defaults to zip's directory)
        extract_pattern: Optional list of glob patterns to extract
                        Example: ["*.txt", "docs/*.md", "images/**/*.png"]

    Returns:
        Dictionary with extraction message and list of extracted files, or error message

    Examples:
        >>> # Example 1: Extract all files from a zip
        >>> unzip_files('/home/user/archive.zip', '/home/user/extracted')
        {
            "message": "Successfully extracted 15 files from '/home/user/archive.zip' to '/home/user/extracted'",
            "extracted_files": ["/home/user/extracted/file1.txt", "/home/user/extracted/docs/readme.md", ...]
        }

        >>> # Example 2: Extract only specific file types using patterns
        >>> unzip_files('/home/user/project.zip', '/home/user/code', extract_pattern=["*.py", "*.json"])
        {
            "message": "Successfully extracted 8 files from '/home/user/project.zip' to '/home/user/code'",
            "extracted_files": ["/home/user/code/main.py", "/home/user/code/config.json", ...]
        }

        >>> # Example 3: Error handling for missing zip file
        >>> unzip_files('/home/user/missing.zip')
        "Error: Zip file '/home/user/missing.zip' does not exist"

        >>> # Example 4: No matching files scenario
        >>> unzip_files('/home/user/docs.zip', extract_pattern=["*.cpp"])
        "No files were extracted from '/home/user/docs.zip'"

    Notes:
        - If extract_dir is not specified, files extract to the same directory as the zip
        - The extract directory will be created if it doesn't exist
        - extract_pattern supports glob-style patterns (*, **, ?, [seq], [!seq])
        - Returns both a success message and a list of all extracted files
        - Returns clear error messages for troubleshooting
    """
    if not os.path.exists(zip_path):
        return f"Error: Zip file '{zip_path}' does not exist"

    try:
        # Default extract directory is the same directory as the zip file
        if extract_dir is None:
            extract_dir = os.path.dirname(zip_path) or "."

        # Create extraction directory if it doesn't exist
        if not os.path.exists(extract_dir):
            os.makedirs(extract_dir)

        extracted_files = []
        with zipfile.ZipFile(zip_path, "r") as zipf:
            # Get list of files in the zip
            file_list = zipf.namelist()

            # Filter files if extract_pattern is provided
            if extract_pattern:
                import fnmatch

                filtered_files = []
                for file_path in file_list:
                    for pattern in extract_pattern:
                        if fnmatch.fnmatch(file_path, pattern):
                            filtered_files.append(file_path)
                            break
                file_list = filtered_files

            # Extract the files
            for file_path in file_list:
                zipf.extract(file_path, extract_dir)
                extracted_files.append(os.path.join(extract_dir, file_path))

        if not extracted_files:
            return f"No files were extracted from '{zip_path}'"

        return {
            "message": f"Successfully extracted {len(extracted_files)} files from '{zip_path}' to '{extract_dir}'",
            "extracted_files": extracted_files,
        }
    except Exception as e:
        return f"Error extracting files: {str(e)}"


@register_tool(tags=["file_handling"])
def search_files(
    directory: str,
    name_pattern: Optional[str] = None,
    content_pattern: Optional[str] = None,
    extension: Optional[str] = None,
    max_size: Optional[int] = None,
    min_size: Optional[int] = None,
    recursive: bool = True,
    use_regex: bool = False,
    max_results: int = 100,
):
    """
    Advanced multi-criteria file search with powerful filtering capabilities.

    This comprehensive search tool allows finding files based on any combination of:
    - Filename patterns
    - Content patterns (text search within files)
    - File extensions
    - Size constraints
    - Directory depth options

    Args:
        directory: Directory to search in
        name_pattern: Pattern to match in filenames (optional)
        content_pattern: Pattern to match in file contents (optional)
        extension: File extension to filter by (e.g., '.txt') (optional)
        max_size: Maximum file size in bytes (optional)
        min_size: Minimum file size in bytes (optional)
        recursive: Whether to search recursively in subdirectories (defaults to True)
        use_regex: Whether to use regex for pattern matching (defaults to False)
        max_results: Maximum number of results to return (defaults to 100)

    Returns:
        List of matching file paths, or error message string

    Examples:
        >>> # Example 1: Simple extension-based search
        >>> search_files('/home/user/project', extension='.py')
        ['/home/user/project/src/main.py', '/home/user/project/tests/test_utils.py']

        >>> # Example 2: Find files by name pattern
        >>> search_files('/home/user/documents', name_pattern='report')
        ['/home/user/documents/annual_report_2023.pdf', '/home/user/documents/monthly_report.docx']

        >>> # Example 3: Content-based search
        >>> search_files('/home/user/code', content_pattern='TODO:')
        ['/home/user/code/app.js', '/home/user/code/utils/helpers.js']

        >>> # Example 4: Complex search with multiple criteria
        >>> search_files(
        ...     '/home/user/project',
        ...     extension='.py',
        ...     content_pattern='def test_',
        ...     max_size=50000,
        ...     recursive=True,
        ...     use_regex=True
        ... )
        ['/home/user/project/tests/test_api.py', '/home/user/project/tests/test_models.py']

        >>> # Example 5: Non-recursive search in current directory only
        >>> search_files('/home/user/downloads', extension='.zip', recursive=False)
        ['/home/user/downloads/archive.zip', '/home/user/downloads/backup.zip']

        >>> # Example 6: Using regex patterns
        >>> search_files('/home/user/logs', name_pattern=r'log_\d{4}-\d{2}-\d{2}\.txt$', use_regex=True)
        ['/home/user/logs/log_2023-01-15.txt', '/home/user/logs/log_2023-02-01.txt']

        >>> # Example 7: Size constrained search
        >>> search_files('/home/user/media', extension='.mp4', min_size=1048576, max_size=10485760)
        ['/home/user/media/clip1.mp4', '/home/user/media/clip2.mp4']

    Notes:
        - All criteria are optional - provide only what you need for your search
        - If content_pattern is provided, the tool will need to read each file's contents
        - For case-insensitive searches, use regex mode with the appropriate flags
        - The search will stop after finding max_results matching files
        - Files that can't be read or accessed are simply skipped
        - Combining filename and content patterns creates an AND condition
        - Returns a simple error message if the specified directory doesn't exist
    """
    if not os.path.exists(directory) or not os.path.isdir(directory):
        return f"Error: Directory '{directory}' does not exist or is not a directory"

    matches = []
    count = 0

    # Prepare the file traversal method
    if recursive:
        walk_method = os.walk(directory)
    else:
        # For non-recursive search, create a generator that only yields the top directory
        def walk_single_dir(dir_path):
            files = []
            dirs = []
            for entry in os.listdir(dir_path):
                full_path = os.path.join(dir_path, entry)
                if os.path.isfile(full_path):
                    files.append(entry)
                elif os.path.isdir(full_path):
                    dirs.append(entry)
            yield (dir_path, dirs, files)

        walk_method = walk_single_dir(directory)

    # Compile regex patterns if needed
    name_regex = None
    content_regex = None
    if use_regex:
        if name_pattern:
            name_regex = re.compile(name_pattern)
        if content_pattern:
            content_regex = re.compile(content_pattern)

    # Walk through directories
    for root, _, files in walk_method:
        for filename in files:
            if count >= max_results:
                break

            file_path = os.path.join(root, filename)

            # Check file extension
            if extension and not filename.endswith(extension):
                continue

            # Check filename pattern
            if name_pattern:
                if use_regex:
                    if not name_regex.search(filename):
                        continue
                elif name_pattern not in filename:
                    continue

            # Check file size
            try:
                file_size = os.path.getsize(file_path)
                if max_size is not None and file_size > max_size:
                    continue
                if min_size is not None and file_size < min_size:
                    continue
            except:
                continue  # Skip if we can't get the file size

            # Check content pattern if specified
            if content_pattern:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        if use_regex:
                            if not content_regex.search(content):
                                continue
                        elif content_pattern not in content:
                            continue
                except:
                    continue  # Skip if we can't read the file

            # If we got here, the file matches all criteria
            matches.append(file_path)
            count += 1

            if count >= max_results:
                break

    return matches
