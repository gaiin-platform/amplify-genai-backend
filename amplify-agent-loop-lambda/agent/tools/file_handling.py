import re
import os

from agent.components.tool import register_tool


@register_tool(tags=["file_handling"])
def get_current_directory(action_context):
    wd = action_context.get('work_directory')
    if not wd:
        wd = os.getcwd()
    return wd

@register_tool(tags=["file_handling"])
def get_writeable_directory(action_context):
    wd = action_context.get('work_directory')
    if not wd:
        wd = os.getcwd()
    return wd


@register_tool(tags=["file_handling"])
def list_files_in_directory(directory: str):
    return os.listdir(directory)


# --- New file handling utilities ---

@register_tool(tags=["file_handling"])
def write_file_from_string(file_path: str, content: str, mode: str = "w"):
    """Writes string content to a file."""
    with open(file_path, mode, encoding="utf-8") as f:
        f.write(content)
    return f"Content written to {file_path}"


@register_tool(tags=["file_handling"])
def read_file(file_path: str):
    """Reads entire content of a file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@register_tool(tags=["file_handling"])
def read_file_partial(file_path: str, length: int, start: int = 0):
    """Reads a portion of a file from a starting position with character length limit."""
    with open(file_path, "r", encoding="utf-8") as f:
        f.seek(start)
        return f.read(length)


@register_tool(tags=["file_handling"])
def search_files_recursive(search_root: str, pattern: str, use_regex: bool = False):
    """Searches for a string or regex pattern in all files under a directory recursively."""
    matches = []
    for root, _, files in os.walk(search_root):
        for filename in files:
            file_path = os.path.join(root, filename)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if (use_regex and re.search(pattern, content)) or (not use_regex and pattern in content):
                        matches.append(file_path)
            except Exception as e:
                continue  # skip unreadable files
    return matches

@register_tool(tags=["file_handling"])
def get_directory_structure(path: str):
    """Recursively builds a JSON-serializable object representing the directory structure, including only directories."""
    def build_tree(current_path):
        tree = {"name": os.path.basename(current_path), "path": current_path, "type": "directory"}
        tree["children"] = []
        for entry in os.listdir(current_path):
            full_path = os.path.join(current_path, entry)
            if os.path.isdir(full_path):
                tree["children"].append(build_tree(full_path))
        return tree

    return build_tree(path)

