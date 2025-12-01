"""
Simple MCP Integration for Chat Endpoint

This provides working MCP tool integration for the chat endpoint without
requiring complex dependencies. It provides real tool execution for
filesystem and memory operations.
"""

import os
import json
import logging
import subprocess
import tempfile
from datetime import datetime
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class SimpleMCPChatIntegration:
    """Simple MCP integration for chat that actually works."""

    def __init__(self):
        self.available_tools = self._get_available_tools()

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get available MCP tools formatted for AI function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "filesystem.read_file",
                    "description": "Read the contents of a file from the filesystem",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to read (absolute or relative)"
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "filesystem.write_file",
                    "description": "Write content to a file on the filesystem",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to write (absolute or relative)"
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write to the file"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "filesystem.list_directory",
                    "description": "List the contents of a directory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the directory to list (absolute or relative, defaults to current directory)"
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "filesystem.create_directory",
                    "description": "Create a new directory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the directory to create"
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "filesystem.file_exists",
                    "description": "Check if a file or directory exists",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to check for existence"
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "system.run_command",
                    "description": "Execute a safe system command (read-only operations like ls, cat, find, etc.)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Safe command to execute (no destructive operations)"
                            }
                        },
                        "required": ["command"]
                    }
                }
            }
        ]

    def get_tools_for_ai(self) -> List[Dict[str, Any]]:
        """Get tools formatted for AI model function calling."""
        return self.available_tools

    async def execute_function_call(self, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an MCP function call."""
        logger.info(f"Executing MCP function: {function_name} with args: {arguments}")

        try:
            if function_name == "filesystem.read_file":
                return await self._read_file(arguments.get("path", ""))

            elif function_name == "filesystem.write_file":
                return await self._write_file(
                    arguments.get("path", ""),
                    arguments.get("content", "")
                )

            elif function_name == "filesystem.list_directory":
                return await self._list_directory(arguments.get("path", "."))

            elif function_name == "filesystem.create_directory":
                return await self._create_directory(arguments.get("path", ""))

            elif function_name == "filesystem.file_exists":
                return await self._file_exists(arguments.get("path", ""))

            elif function_name == "system.run_command":
                return await self._run_safe_command(arguments.get("command", ""))

            else:
                return {
                    "success": False,
                    "error": f"Unknown function: {function_name}",
                    "content": None
                }

        except Exception as e:
            logger.error(f"Error executing function {function_name}: {e}")
            return {
                "success": False,
                "error": str(e),
                "content": None
            }

    async def _read_file(self, path: str) -> Dict[str, Any]:
        """Read a file from the filesystem."""
        try:
            # Security: Resolve the path and check if it's reasonable
            if not path or ".." in path or path.startswith("/etc") or path.startswith("/root"):
                return {
                    "success": False,
                    "error": "Path not allowed for security reasons",
                    "content": None
                }

            # Make path relative to current working directory if not absolute
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)

            if not os.path.exists(path):
                return {
                    "success": False,
                    "error": f"File not found: {path}",
                    "content": None
                }

            if not os.path.isfile(path):
                return {
                    "success": False,
                    "error": f"Path is not a file: {path}",
                    "content": None
                }

            # Read the file (limit size for safety)
            file_size = os.path.getsize(path)
            if file_size > 1024 * 1024:  # 1MB limit
                return {
                    "success": False,
                    "error": f"File too large ({file_size} bytes). Maximum size is 1MB.",
                    "content": None
                }

            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            return {
                "success": True,
                "content": content,
                "path": path,
                "size": file_size
            }

        except UnicodeDecodeError:
            return {
                "success": False,
                "error": "File is not a text file or uses unsupported encoding",
                "content": None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "content": None
            }

    async def _write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write content to a file."""
        try:
            # Security checks
            if not path or ".." in path or path.startswith("/etc") or path.startswith("/root"):
                return {
                    "success": False,
                    "error": "Path not allowed for security reasons"
                }

            # Make path relative to current working directory if not absolute
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)

            # Create directory if it doesn't exist
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)

            # Write the file
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)

            return {
                "success": True,
                "message": f"Successfully wrote {len(content)} characters to {path}",
                "path": path,
                "size": len(content)
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _list_directory(self, path: str = ".") -> Dict[str, Any]:
        """List directory contents."""
        try:
            # Security checks
            if ".." in path or (path.startswith("/") and not path.startswith(os.getcwd())):
                return {
                    "success": False,
                    "error": "Path not allowed for security reasons"
                }

            # Make path relative to current working directory if not absolute
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)

            if not os.path.exists(path):
                return {
                    "success": False,
                    "error": f"Directory not found: {path}"
                }

            if not os.path.isdir(path):
                return {
                    "success": False,
                    "error": f"Path is not a directory: {path}"
                }

            # List directory contents
            items = []
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                try:
                    stat = os.stat(item_path)
                    items.append({
                        "name": item,
                        "type": "directory" if os.path.isdir(item_path) else "file",
                        "size": stat.st_size if os.path.isfile(item_path) else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
                except:
                    items.append({
                        "name": item,
                        "type": "unknown",
                        "size": None,
                        "modified": None
                    })

            return {
                "success": True,
                "path": path,
                "items": sorted(items, key=lambda x: (x["type"] == "file", x["name"].lower()))
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _create_directory(self, path: str) -> Dict[str, Any]:
        """Create a directory."""
        try:
            # Security checks
            if not path or ".." in path or path.startswith("/etc") or path.startswith("/root"):
                return {
                    "success": False,
                    "error": "Path not allowed for security reasons"
                }

            # Make path relative to current working directory if not absolute
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)

            os.makedirs(path, exist_ok=True)

            return {
                "success": True,
                "message": f"Directory created: {path}",
                "path": path
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _file_exists(self, path: str) -> Dict[str, Any]:
        """Check if a file or directory exists."""
        try:
            # Make path relative to current working directory if not absolute
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)

            exists = os.path.exists(path)
            item_type = None

            if exists:
                if os.path.isfile(path):
                    item_type = "file"
                elif os.path.isdir(path):
                    item_type = "directory"
                else:
                    item_type = "other"

            return {
                "success": True,
                "exists": exists,
                "path": path,
                "type": item_type
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _run_safe_command(self, command: str) -> Dict[str, Any]:
        """Execute a safe system command."""
        try:
            # List of allowed safe commands
            safe_commands = [
                "ls", "dir", "pwd", "whoami", "date", "echo", "cat", "head", "tail",
                "find", "grep", "wc", "sort", "uniq", "which", "whereis", "file",
                "stat", "du", "df", "ps", "top", "id", "groups", "env"
            ]

            # Extract the base command
            base_command = command.split()[0] if command else ""

            if not base_command or base_command not in safe_commands:
                return {
                    "success": False,
                    "error": f"Command '{base_command}' is not allowed. Safe commands: {', '.join(safe_commands)}"
                }

            # Execute the command with timeout
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.getcwd()
            )

            return {
                "success": True,
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out after 10 seconds"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Global instance
_mcp_chat_integration = None


def get_mcp_chat_integration() -> SimpleMCPChatIntegration:
    """Get the global MCP chat integration instance."""
    global _mcp_chat_integration
    if _mcp_chat_integration is None:
        _mcp_chat_integration = SimpleMCPChatIntegration()
    return _mcp_chat_integration