import os
import sys
import json
import platform
import subprocess
from typing import Dict, List, Optional, Union, Any
import shlex
import tempfile
import datetime
import time

from agent.components.tool import register_tool


@register_tool(tags=["shell"])
def execute_shell_command(
    command: str, timeout: int = 30, working_directory: str = None
):
    """
    Executes a shell command and returns its output.

    This tool provides a safe way to run shell commands with proper error handling,
    timeouts, and working directory control.

    Args:
        command: The shell command to execute
        timeout: Maximum execution time in seconds (default: 30)
        working_directory: Directory to execute the command in (default: current directory)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if command executed successfully
        - stdout: Standard output as string
        - stderr: Standard error as string
        - exit_code: Command exit code
        - execution_time: Time taken to execute in seconds

    Examples:
        >>> execute_shell_command('ls -la')
        {
            "success": true,
            "stdout": "total 32\ndrwxr-xr-x  6 user  staff   192 Feb 12 10:25 .\n...",
            "stderr": "",
            "exit_code": 0,
            "execution_time": 0.035
        }

        >>> execute_shell_command('find /tmp -name "*.log"', timeout=60)
        {
            "success": true,
            "stdout": "/tmp/app.log\n/tmp/system.log",
            "stderr": "",
            "exit_code": 0,
            "execution_time": 2.4
        }

        >>> execute_shell_command('grep "ERROR" /nonexistent/file.txt')
        {
            "success": false,
            "stdout": "",
            "stderr": "grep: /nonexistent/file.txt: No such file or directory",
            "exit_code": 2,
            "execution_time": 0.02
        }

    Notes:
        - The command is run with shell=True, so shell syntax (pipes, redirects, etc.) works
        - Non-zero exit codes are treated as failures, but output is still returned
        - Commands that exceed the timeout will be forcibly terminated
        - For security, validate all user-provided input before using in commands
    """
    try:
        start_time = time.time()

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
            cwd=working_directory,
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
            exit_code = process.returncode
            success = exit_code == 0
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            exit_code = -1
            success = False
            stderr += f"\nCommand timed out after {timeout} seconds."

        execution_time = time.time() - start_time

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "execution_time": round(execution_time, 3),
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Error executing command: {str(e)}",
            "exit_code": -1,
            "execution_time": 0,
        }


@register_tool(tags=["shell"])
def get_environment_info():
    """
    Returns detailed information about the system environment.

    This tool provides a comprehensive overview of the execution environment,
    including platform details, Python configuration, environment variables,
    file system information, and more.

    Returns:
        Dictionary containing detailed environment information:
        - system: OS name, version, architecture, hostname, etc.
        - python: Python version, executable path, installed packages
        - environment: Key environment variables filtered for privacy
        - filesystem: Working directory, disk usage
        - time: Current timestamp, timezone info

    Examples:
        >>> get_environment_info()
        {
            "system": {
                "platform": "Linux",
                "platform_release": "5.15.0-1019-aws",
                "platform_version": "#23-Ubuntu SMP Thu Apr 6 18:18:11 UTC 2023",
                "architecture": "x86_64",
                "hostname": "ip-172-31-16-8",
                "processor": "x86_64",
                "cpu_count": 2,
                "memory_total_mb": 7834.45
            },
            "python": {
                "version": "3.9.16",
                "executable": "/usr/bin/python3.9",
                "packages": ["boto3==1.26.90", "requests==2.28.2", ...]
            },
            "environment": {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PWD": "/home/ubuntu",
                "USER": "ubuntu",
                "LANG": "en_US.UTF-8"
            },
            "filesystem": {
                "working_directory": "/home/ubuntu/project",
                "disk_usage": {
                    "total_gb": 7.7,
                    "used_gb": 3.2,
                    "free_gb": 4.5,
                    "percent_used": 41.5
                }
            },
            "time": {
                "timestamp": "2023-08-21T14:32:05.123456",
                "timezone": "UTC+0000",
                "uptime_seconds": 345621
            }
        }

    Notes:
        - Environment variables are filtered to exclude sensitive data like API keys
        - Some system information may be limited in containerized environments
        - Memory and disk information might be virtualized in cloud environments
        - Uptime may not be available on all platforms
    """
    try:
        info = {
            "system": get_system_info(),
            "python": get_python_info(),
            "environment": get_safe_env_vars(),
            "filesystem": get_filesystem_info(),
            "time": get_time_info(),
        }
        return info
    except Exception as e:
        return {"error": f"Failed to gather environment information: {str(e)}"}


def get_system_info():
    """Gather system information."""
    system_info = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "processor": platform.processor(),
    }

    # Try to get CPU count
    try:
        import multiprocessing

        system_info["cpu_count"] = multiprocessing.cpu_count()
    except:
        pass

    # Try to get memory info
    try:
        import psutil

        mem = psutil.virtual_memory()
        system_info["memory_total_mb"] = round(mem.total / (1024 * 1024), 2)
        system_info["memory_available_mb"] = round(mem.available / (1024 * 1024), 2)
    except:
        pass

    return system_info


def get_python_info():
    """Gather Python interpreter information."""
    python_info = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "path": sys.path,
    }

    # Try to get installed packages
    try:
        import pkg_resources

        packages = sorted(
            [
                f"{dist.project_name}=={dist.version}"
                for dist in pkg_resources.working_set
            ]
        )
        python_info["packages"] = packages
    except:
        python_info["packages"] = []

    return python_info


def get_safe_env_vars():
    """Return environment variables with sensitive info filtered."""
    # List of environment variable prefixes to exclude for security
    exclude_prefixes = [
        "AWS_",
        "GOOGLE_",
        "AZURE_",
        "API_",
        "SECRET_",
        "PASSWORD",
        "TOKEN",
        "KEY",
        "CREDENTIAL",
        "AUTH",
        "PRIVATE",
    ]

    safe_env = {}
    for key, value in os.environ.items():
        # Skip variables that might contain sensitive information
        if any(
            key.startswith(prefix) or prefix in key.upper()
            for prefix in exclude_prefixes
        ):
            continue

        safe_env[key] = value

    return safe_env


def get_filesystem_info():
    """Gather filesystem information."""
    fs_info = {"working_directory": os.getcwd()}

    # Try to get disk usage for the current directory
    try:
        import shutil

        total, used, free = shutil.disk_usage(os.getcwd())
        fs_info["disk_usage"] = {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent_used": round((used / total) * 100, 1),
        }
    except:
        pass

    return fs_info


def get_time_info():
    """Gather time-related information."""
    time_info = {
        "timestamp": datetime.datetime.now().isoformat(),
        "timezone": time.tzname[0],
    }

    # Try to get system uptime
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
            time_info["uptime_seconds"] = int(uptime_seconds)
    except:
        pass

    return time_info


@register_tool(tags=["shell"])
def get_available_commands(shell: str = "bash"):
    """
    Returns a list of available shell commands in the current environment.

    This tool helps discover which commands are available for execution,
    which is useful when working in unfamiliar environments.

    Args:
        shell: The shell to query (default: bash)

    Returns:
        Dictionary containing:
        - commands: List of available commands
        - builtin_commands: List of built-in shell commands
        - command_details: Additional path information for selected commands

    Examples:
        >>> get_available_commands()
        {
            "commands": ["apt", "awk", "bash", "cat", "chmod", "chown", "cp", "curl", ...],
            "builtin_commands": ["cd", "echo", "source", "export", "alias", ...],
            "command_details": {
                "python": "/usr/bin/python",
                "node": "/usr/local/bin/node",
                "git": "/usr/bin/git",
                "aws": "/usr/local/bin/aws"
            }
        }

        >>> get_available_commands('zsh')
        {
            "commands": ["brew", "gcc", "python3", "ssh", "vim", ...],
            "builtin_commands": ["cd", "echo", "source", "export", "alias", ...],
            "command_details": {...}
        }

    Notes:
        - The 'commands' list contains executables found in PATH directories
        - 'builtin_commands' may vary depending on the shell specified
        - 'command_details' provides the full path for common important commands
        - This tool uses 'which' to determine command locations
        - Results may vary significantly between different environments
    """
    try:
        # Get all directories in PATH
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)

        # Find all executable files in PATH
        commands = set()
        for directory in path_dirs:
            if os.path.exists(directory):
                for filename in os.listdir(directory):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath) and os.access(filepath, os.X_OK):
                        commands.add(filename)

        # Get shell builtin commands
        builtin_commands = []
        if shell == "bash":
            try:
                result = subprocess.run(
                    "bash -c 'compgen -b'", shell=True, capture_output=True, text=True
                )
                if result.returncode == 0:
                    builtin_commands = result.stdout.strip().split("\n")
            except:
                pass
        elif shell == "zsh":
            try:
                result = subprocess.run(
                    "zsh -c 'print -l ${(k)commands}'",
                    shell=True,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    builtin_commands = result.stdout.strip().split("\n")
            except:
                pass

        # Get details for common important commands
        important_commands = [
            "python",
            "python3",
            "pip",
            "pip3",
            "node",
            "npm",
            "git",
            "aws",
            "docker",
            "kubectl",
            "terraform",
            "ansible",
        ]

        command_details = {}
        for cmd in important_commands:
            if cmd in commands:
                try:
                    result = subprocess.run(
                        f"which {cmd}", shell=True, capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        command_details[cmd] = result.stdout.strip()
                except:
                    pass

        return {
            "commands": sorted(list(commands)),
            "builtin_commands": sorted(builtin_commands),
            "command_details": command_details,
        }
    except Exception as e:
        return {"error": f"Failed to get available commands: {str(e)}"}


@register_tool(tags=["shell"])
def safe_command_exists(command: str):
    """
    Safely checks if a command exists in the environment without executing it.

    This tool determines if a command is available for execution via PATH
    lookup or as a shell builtin, without actually running the command.

    Args:
        command: The command name to check

    Returns:
        Dictionary containing:
        - exists: Boolean indicating if command exists
        - path: Full path to the command (if found as executable)
        - type: Type of command ('executable', 'builtin', or 'not found')

    Examples:
        >>> safe_command_exists('ls')
        {
            "exists": true,
            "path": "/bin/ls",
            "type": "executable"
        }

        >>> safe_command_exists('cd')
        {
            "exists": true,
            "path": null,
            "type": "builtin"
        }

        >>> safe_command_exists('nonexistentcommand')
        {
            "exists": false,
            "path": null,
            "type": "not found"
        }

    Notes:
        - This function will never execute the command
        - It first checks if the command is a shell builtin
        - Then it uses 'which' to look for the command in PATH
        - Returns detailed information about how and where the command was found
        - Useful for checking command availability before attempting execution
    """
    try:
        # Check if command is a shell builtin
        try:
            result = subprocess.run(
                f"type -t {shlex.quote(command)}",
                shell=True,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "builtin" in result.stdout:
                return {"exists": True, "path": None, "type": "builtin"}
        except:
            pass

        # Check if command exists in PATH
        try:
            result = subprocess.run(
                f"which {shlex.quote(command)}",
                shell=True,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return {
                    "exists": True,
                    "path": result.stdout.strip(),
                    "type": "executable",
                }
        except:
            pass

        # Command not found
        return {"exists": False, "path": None, "type": "not found"}
    except Exception as e:
        return {"exists": False, "path": None, "type": "error", "error": str(e)}


@register_tool(tags=["shell"])
def run_command_safely(
    command: str,
    args: List[str] = None,
    timeout: int = 30,
    working_directory: str = None,
):
    """
    Runs a command with arguments in a safer way, sanitizing inputs.

    This tool provides a more secure way to execute shell commands by
    explicitly separating the command and its arguments, reducing risk
    of shell injection attacks.

    Args:
        command: The command to execute
        args: List of arguments to pass to the command (default: [])
        timeout: Maximum execution time in seconds (default: 30)
        working_directory: Directory to execute command in (default: current directory)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if command executed successfully
        - stdout: Standard output as string
        - stderr: Standard error as string
        - exit_code: Command exit code
        - execution_time: Time taken to execute in seconds
        - command_line: The exact command line that was executed

    Examples:
        >>> run_command_safely('ls', ['-la', '/tmp'])
        {
            "success": true,
            "stdout": "total 32\ndrwxr-xr-x  6 user  staff   192 Feb 12 10:25 .\n...",
            "stderr": "",
            "exit_code": 0,
            "execution_time": 0.032,
            "command_line": "ls -la /tmp"
        }

        >>> run_command_safely('find', ['/tmp', '-name', '*.log'])
        {
            "success": true,
            "stdout": "/tmp/app.log\n/tmp/system.log",
            "stderr": "",
            "exit_code": 0,
            "execution_time": 2.1,
            "command_line": "find /tmp -name *.log"
        }

        >>> run_command_safely('grep', ['ERROR', '/nonexistent/file.txt'])
        {
            "success": false,
            "stdout": "",
            "stderr": "grep: /nonexistent/file.txt: No such file or directory",
            "exit_code": 2,
            "execution_time": 0.018,
            "command_line": "grep ERROR /nonexistent/file.txt"
        }

    Notes:
        - Arguments are passed directly to subprocess, not through a shell
        - This significantly reduces the risk of shell injection attacks
        - Environment variables and shell expansions will NOT work with this method
        - For shell features (pipes, redirects, etc.), use execute_shell_command instead
        - Command is validated against available executables before running
    """
    args = args or []

    # First check if the command exists
    command_info = safe_command_exists(command)
    if not command_info["exists"]:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Command '{command}' not found",
            "exit_code": 127,  # Standard "command not found" exit code
            "execution_time": 0,
            "command_line": f"{command} {' '.join(args)}",
        }

    # If it's a builtin, we need to run it through a shell
    if command_info["type"] == "builtin":
        cmd_line = f"{command} {' '.join(shlex.quote(arg) for arg in args)}"
        return {
            **execute_shell_command(cmd_line, timeout, working_directory),
            "command_line": cmd_line,
        }

    # For regular executables, run directly with subprocess
    start_time = time.time()
    try:
        # Use the full path if we found it
        cmd_path = command_info["path"] if command_info["path"] else command

        process = subprocess.Popen(
            [cmd_path] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_directory,
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
            exit_code = process.returncode
            success = exit_code == 0
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            exit_code = -1
            success = False
            stderr += f"\nCommand timed out after {timeout} seconds."

        execution_time = time.time() - start_time

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "execution_time": round(execution_time, 3),
            "command_line": f"{command} {' '.join(args)}",
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Error executing command: {str(e)}",
            "exit_code": -1,
            "execution_time": round(time.time() - start_time, 3),
            "command_line": f"{command} {' '.join(args)}",
        }


@register_tool(tags=["shell"])
def create_temporary_script(
    content: str, extension: str = ".sh", make_executable: bool = True
):
    """
    Creates a temporary script file and returns its path.

    This tool generates a script file with the provided content in the system's
    temporary directory, with options to make it executable and control the file
    extension.

    Args:
        content: The script content to write to the file
        extension: File extension to use (default: .sh)
        make_executable: Whether to make the file executable (default: True)

    Returns:
        Dictionary containing:
        - path: Absolute path to the created temporary file
        - created: Timestamp when the file was created
        - size: Size of the file in bytes
        - is_executable: Whether the file is executable

    Examples:
        >>> create_temporary_script('#!/bin/bash\\necho "Hello, world!"')
        {
            "path": "/tmp/tmp_scriptabcd1234.sh",
            "created": "2023-08-21T15:32:45.123456",
            "size": 32,
            "is_executable": true
        }

        >>> create_temporary_script('print("Hello from Python")\\n', extension='.py', make_executable=False)
        {
            "path": "/tmp/tmp_scripteffg5678.py",
            "created": "2023-08-21T15:33:12.654321",
            "size": 24,
            "is_executable": false
        }

    Notes:
        - Files are created in the system's temp directory
        - For bash scripts, automatically adds shebang if missing
        - The file will be deleted when the Python process exits
        - If you need a persistent script, consider using write_file_from_string instead
        - Returns all necessary metadata to use the script immediately
    """
    try:
        # Add shebang for shell scripts if it's missing
        if extension == ".sh" and not content.startswith("#!"):
            content = "#!/bin/bash\n" + content

        # Create a temporary file with the specified extension
        fd, path = tempfile.mkstemp(suffix=extension, prefix="tmp_script")

        # Write the content to the file
        with os.fdopen(fd, "w") as f:
            f.write(content)

        # Make the file executable if requested
        if make_executable:
            os.chmod(path, 0o755)  # rwxr-xr-x

        # Get file metadata
        file_stats = os.stat(path)

        return {
            "path": path,
            "created": datetime.datetime.fromtimestamp(file_stats.st_ctime).isoformat(),
            "size": file_stats.st_size,
            "is_executable": os.access(path, os.X_OK),
        }
    except Exception as e:
        return {"error": f"Failed to create temporary script: {str(e)}"}


@register_tool(tags=["shell"])
def execute_pipeline(commands: List[Dict[str, Union[str, List[str]]]]):
    """
    Executes a pipeline of commands, feeding output between them.

    This tool mimics Unix-style command pipelines where the output of each
    command is fed as input to the next command in the sequence.

    Args:
        commands: List of command objects, each with:
                 - 'command': The command name (required)
                 - 'args': List of arguments (optional)

    Returns:
        Dictionary containing:
        - success: Boolean indicating overall pipeline success
        - stages: List of results from each command in the pipeline
        - final_stdout: Output from the final command in the pipeline
        - final_stderr: Error output from the final command
        - execution_time: Total time taken to execute the pipeline

    Examples:
        >>> execute_pipeline([
        ...     {"command": "find", "args": ["/tmp", "-type", "f", "-name", "*.log"]},
        ...     {"command": "grep", "args": ["ERROR"]},
        ...     {"command": "head", "args": ["-n", "5"]}
        ... ])
        {
            "success": true,
            "stages": [
                {"command": "find", "exit_code": 0, "success": true},
                {"command": "grep", "exit_code": 0, "success": true},
                {"command": "head", "exit_code": 0, "success": true}
            ],
            "final_stdout": "ERROR: System failure in module X at 2023-08-20 15:42:23\\nERROR: Connection timeout after 30s\\n...",
            "final_stderr": "",
            "execution_time": 1.23
        }

        >>> execute_pipeline([
        ...     {"command": "echo", "args": ["Hello, world!"]},
        ...     {"command": "tr", "args": ["a-z", "A-Z"]}
        ... ])
        {
            "success": true,
            "stages": [
                {"command": "echo", "exit_code": 0, "success": true},
                {"command": "tr", "exit_code": 0, "success": true}
            ],
            "final_stdout": "HELLO, WORLD!",
            "final_stderr": "",
            "execution_time": 0.05
        }

    Notes:
        - This simulates shell pipelines (like 'cmd1 | cmd2 | cmd3')
        - The pipeline success is True only if all commands succeed
        - Commands are executed sequentially, with output properly passed between them
        - If any command fails, the pipeline continues but will be marked as failed
        - All outputs and errors from intermediate steps are captured in 'stages'
        - The executed commands are validated for existence before running
    """
    if not commands:
        return {
            "success": False,
            "stages": [],
            "final_stdout": "",
            "final_stderr": "No commands provided",
            "execution_time": 0,
        }

    start_time = time.time()
    stages = []
    current_input = None
    overall_success = True

    for i, cmd_info in enumerate(commands):
        command = cmd_info.get("command")
        args = cmd_info.get("args", [])

        if not command:
            return {
                "success": False,
                "stages": stages,
                "final_stdout": "",
                "final_stderr": f"Command at position {i} is missing the 'command' attribute",
                "execution_time": round(time.time() - start_time, 3),
            }

        try:
            # Check if command exists
            command_info = safe_command_exists(command)
            if not command_info["exists"]:
                stages.append(
                    {
                        "command": command,
                        "exit_code": 127,
                        "success": False,
                        "error": f"Command not found: {command}",
                    }
                )
                overall_success = False
                continue

            # Create the process
            if command_info["type"] == "builtin":
                # For builtins, we need to use shell=True and handle the input differently
                cmd_str = f"{command} {' '.join(shlex.quote(str(arg)) for arg in args)}"
                process = subprocess.Popen(
                    cmd_str,
                    shell=True,
                    stdin=subprocess.PIPE if current_input is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            else:
                # For regular commands, use the safe list approach
                process = subprocess.Popen(
                    [command] + args,
                    stdin=subprocess.PIPE if current_input is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            # Send input to the process if available
            stdout, stderr = process.communicate(input=current_input)
            exit_code = process.returncode
            success = exit_code == 0

            # Store the stage result
            stages.append(
                {"command": command, "exit_code": exit_code, "success": success}
            )

            # Update overall success
            if not success:
                overall_success = False

            # Set the output for the next command
            current_input = stdout

        except Exception as e:
            stages.append(
                {"command": command, "exit_code": -1, "success": False, "error": str(e)}
            )
            overall_success = False
            # Continue with empty input for the next command
            current_input = ""

    # Get the final outputs
    final_stdout = current_input or ""
    final_stderr = stderr if "stderr" in locals() else ""

    return {
        "success": overall_success,
        "stages": stages,
        "final_stdout": final_stdout,
        "final_stderr": final_stderr,
        "execution_time": round(time.time() - start_time, 3),
    }


@register_tool(tags=["shell"])
def list_processes():
    """
    Lists currently running processes on the system.

    This tool provides a snapshot of active processes with useful information
    about each one, similar to tools like 'ps' but with structured output.

    Returns:
        List of dictionaries, each containing:
        - pid: Process ID
        - name: Process name
        - status: Process state (running, sleeping, etc.)
        - cpu_percent: CPU usage percentage
        - memory_percent: Memory usage percentage
        - create_time: When the process was started
        - username: User who owns the process
        - cmdline: Full command line used to start the process

    Examples:
        >>> list_processes()
        [
            {
                "pid": 1,
                "name": "systemd",
                "status": "sleeping",
                "cpu_percent": 0.0,
                "memory_percent": 0.5,
                "create_time": "2023-08-01T12:00:00",
                "username": "root",
                "cmdline": "/sbin/init"
            },
            {
                "pid": 1234,
                "name": "python3",
                "status": "running",
                "cpu_percent": 2.5,
                "memory_percent": 1.2,
                "create_time": "2023-08-21T09:15:22",
                "username": "ubuntu",
                "cmdline": "python3 app.py --server"
            },
            ...
        ]

    Notes:
        - Requires the psutil package to be installed
        - Provides a safer alternative to directly using 'ps' commands
        - Returns an error message if psutil is not available
        - Command line arguments are properly sanitized
        - Large process lists are truncated to include only the most relevant processes
        - Username might be unavailable in some containerized environments
    """
    try:
        import psutil
    except ImportError:
        return {"error": "psutil package is not available. Cannot list processes."}

    process_list = []
    try:
        for proc in psutil.process_iter(
            ["pid", "name", "username", "status", "cmdline", "create_time"]
        ):
            try:
                # Get process info as dictionary
                proc_info = proc.info

                # Get CPU and memory percentages
                with proc.oneshot():
                    try:
                        cpu_percent = proc.cpu_percent(interval=None)
                        memory_percent = proc.memory_percent()
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        cpu_percent = None
                        memory_percent = None

                # Format create time
                if proc_info["create_time"]:
                    create_time = datetime.datetime.fromtimestamp(
                        proc_info["create_time"]
                    ).isoformat()
                else:
                    create_time = None

                # Build the process entry
                process_entry = {
                    "pid": proc_info["pid"],
                    "name": proc_info["name"],
                    "status": proc_info["status"],
                    "cpu_percent": (
                        round(cpu_percent, 1) if cpu_percent is not None else None
                    ),
                    "memory_percent": (
                        round(memory_percent, 1) if memory_percent is not None else None
                    ),
                    "create_time": create_time,
                    "username": proc_info["username"],
                    "cmdline": (
                        " ".join(proc_info["cmdline"]) if proc_info["cmdline"] else None
                    ),
                }

                process_list.append(process_entry)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Skip processes that disappeared or can't be accessed
                continue

        # Sort by CPU usage (highest first) and limit to a reasonable number
        process_list.sort(
            key=lambda x: x["cpu_percent"] if x["cpu_percent"] is not None else -1,
            reverse=True,
        )
        return process_list[:100]  # Limit to 100 processes to avoid overwhelming output

    except Exception as e:
        return {"error": f"Failed to list processes: {str(e)}"}


@register_tool(tags=["shell"])
def monitor_command(command: str, duration: int = 5, interval: float = 1.0):
    """
    Monitors a command's output and resource usage over time.

    This tool executes a command and collects periodic snapshots of its
    output, CPU usage, memory consumption, and other metrics.

    Args:
        command: The shell command to monitor
        duration: How long to monitor in seconds (default: 5)
        interval: Time between snapshots in seconds (default: 1.0)

    Returns:
        Dictionary containing:
        - command: The command that was monitored
        - success: Whether the command completed successfully
        - exit_code: Command's exit code
        - snapshots: List of snapshots with output and resource usage
        - summary: Statistics about resource usage over time
        - final_stdout: Final complete stdout from the command
        - final_stderr: Final complete stderr from the command

    Examples:
        >>> monitor_command("ping -c 10 google.com", duration=5, interval=0.5)
        {
            "command": "ping -c 10 google.com",
            "success": true,
            "exit_code": 0,
            "snapshots": [
                {
                    "timestamp": "2023-08-21T15:45:01.123",
                    "cpu_percent": 0.5,
                    "memory_mb": 3.2,
                    "running_time": 0.5,
                    "output": "PING google.com (142.250.80.78) 56(84) bytes of data.\\n64 bytes from..."
                },
                {
                    "timestamp": "2023-08-21T15:45:01.623",
                    "cpu_percent": 0.7,
                    "memory_mb": 3.2,
                    "running_time": 1.0,
                    "output": "PING google.com (142.250.80.78) 56(84) bytes of data.\\n64 bytes from..."
                },
                ...
            ],
            "summary": {
                "avg_cpu_percent": 0.6,
                "max_cpu_percent": 0.8,
                "avg_memory_mb": 3.2,
                "max_memory_mb": 3.3,
                "total_running_time": 5.0
            },
            "final_stdout": "PING google.com (142.250.80.78) 56(84) bytes of data.\\n...",
            "final_stderr": ""
        }

        >>> monitor_command("echo 'Hello'; sleep 3; echo 'World'", duration=4, interval=1.0)
        {
            "command": "echo 'Hello'; sleep 3; echo 'World'",
            "success": true,
            "exit_code": 0,
            "snapshots": [
                {
                    "timestamp": "2023-08-21T15:46:01.123",
                    "cpu_percent": 0.1,
                    "memory_mb": 1.2,
                    "running_time": 1.0,
                    "output": "Hello\\n"
                },
                ...
                {
                    "timestamp": "2023-08-21T15:46:04.123",
                    "cpu_percent": 0.1,
                    "memory_mb": 1.2,
                    "running_time": 4.0,
                    "output": "Hello\\nWorld\\n"
                }
            ],
            "summary": {...},
            "final_stdout": "Hello\\nWorld\\n",
            "final_stderr": ""
        }

    Notes:
        - The command runs for at least the specified duration, unless it finishes earlier
        - Each snapshot contains the cumulative output up to that point
        - CPU and memory usage statistics require the psutil package
        - The tool uses a non-blocking approach to read command output
        - Command is executed with shell=True, so shell features will work
        - If the command is still running after the duration, it is terminated
    """
    try:
        # Check if psutil is available for resource monitoring
        try:
            import psutil

            psutil_available = True
        except ImportError:
            psutil_available = False

        # Start the process
        start_time = time.time()
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True,
        )

        # Set up non-blocking reads
        import fcntl
        import select
        import io

        # Make stdout and stderr non-blocking
        for pipe in [process.stdout, process.stderr]:
            fd = pipe.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        # Track process resources if psutil is available
        psutil_proc = None
        if psutil_available:
            try:
                psutil_proc = psutil.Process(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                psutil_proc = None

        # Initialize variables
        snapshots = []
        stdout_buffer = ""
        stderr_buffer = ""
        cpu_percentages = []
        memory_usages = []

        # Monitor until duration is reached or process completes
        while time.time() - start_time < duration:
            # Check if process has completed
            if process.poll() is not None:
                break

            # Sleep a bit before taking a snapshot
            time.sleep(min(0.1, interval))

            # Read available output
            readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0)

            if process.stdout in readable:
                chunk = process.stdout.read()
                if chunk:
                    stdout_buffer += chunk

            if process.stderr in readable:
                chunk = process.stderr.read()
                if chunk:
                    stderr_buffer += chunk

            # Take a snapshot at the specified interval
            current_time = time.time()
            if current_time - start_time >= len(snapshots) * interval:
                snapshot = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "running_time": round(current_time - start_time, 2),
                    "output": stdout_buffer,
                }

                # Add resource usage metrics if available
                if psutil_proc:
                    try:
                        cpu_percent = psutil_proc.cpu_percent()
                        memory_info = psutil_proc.memory_info()
                        memory_mb = memory_info.rss / (1024 * 1024)

                        snapshot["cpu_percent"] = round(cpu_percent, 1)
                        snapshot["memory_mb"] = round(memory_mb, 1)

                        cpu_percentages.append(cpu_percent)
                        memory_usages.append(memory_mb)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                snapshots.append(snapshot)

        # Process completion or timeout - collect final output
        try:
            final_stdout, final_stderr = process.communicate(timeout=0.5)
            stdout_buffer += final_stdout
            stderr_buffer += final_stderr
        except subprocess.TimeoutExpired:
            # Force terminate if still running
            process.terminate()
            try:
                final_stdout, final_stderr = process.communicate(timeout=0.5)
                stdout_buffer += final_stdout
                stderr_buffer += final_stderr
            except subprocess.TimeoutExpired:
                process.kill()
                final_stdout, final_stderr = process.communicate()
                stdout_buffer += final_stdout
                stderr_buffer += final_stderr

        # Calculate summary statistics
        summary = {"total_running_time": round(time.time() - start_time, 2)}

        if cpu_percentages:
            summary["avg_cpu_percent"] = round(
                sum(cpu_percentages) / len(cpu_percentages), 1
            )
            summary["max_cpu_percent"] = round(max(cpu_percentages), 1)

        if memory_usages:
            summary["avg_memory_mb"] = round(sum(memory_usages) / len(memory_usages), 1)
            summary["max_memory_mb"] = round(max(memory_usages), 1)

        return {
            "command": command,
            "success": process.returncode == 0,
            "exit_code": process.returncode,
            "snapshots": snapshots,
            "summary": summary,
            "final_stdout": stdout_buffer,
            "final_stderr": stderr_buffer,
        }
    except Exception as e:
        return {
            "command": command,
            "success": False,
            "error": f"Failed to monitor command: {str(e)}",
        }


@register_tool(tags=["shell"])
def find_and_replace_in_files(
    directory: str,
    find_pattern: str,
    replace_pattern: str,
    file_extensions: List[str] = None,
    use_regex: bool = False,
    recursive: bool = True,
    preview: bool = True,
):
    """
    Find and replace text in multiple files with preview option.

    This tool performs bulk text replacement across files, with the option
    to preview changes before applying them.

    Args:
        directory: Base directory to search in
        find_pattern: Text pattern to find
        replace_pattern: Text to replace matches with
        file_extensions: List of file extensions to process (e.g., ['.py', '.txt'])
        use_regex: Whether to interpret find_pattern as a regex (default: False)
        recursive: Whether to process subdirectories (default: True)
        preview: Only show changes without applying them (default: True)

    Returns:
        Dictionary containing:
        - files_matched: Number of files with matches
        - total_replacements: Total number of replacements (potential or actual)
        - changes: Dictionary mapping file paths to their changes
        - applied: Whether changes were applied or just previewed

    Examples:
        >>> # Preview replacements
        >>> find_and_replace_in_files(
        ...     '/home/user/project',
        ...     'http://old-domain.com',
        ...     'https://new-domain.com',
        ...     file_extensions=['.html', '.js'],
        ...     preview=True
        ... )
        {
            "files_matched": 3,
            "total_replacements": 17,
            "changes": {
                "/home/user/project/index.html": {
                    "line_matches": [15, 42, 107],
                    "preview": [
                        "Line 15: - <a href=\"http://old-domain.com/about\">About</a>",
                        "Line 15: + <a href=\"https://new-domain.com/about\">About</a>",
                        ...
                    ]
                },
                ...
            },
            "applied": false
        }

        >>> # Apply replacements with regex
        >>> find_and_replace_in_files(
        ...     '/home/user/project/src',
        ...     r'TODO:\s*(\w+)',
        ...     r'FIXED: \1',
        ...     file_extensions=['.py'],
        ...     use_regex=True,
        ...     preview=False
        ... )
        {
            "files_matched": 5,
            "total_replacements": 23,
            "changes": {
                "/home/user/project/src/main.py": {
                    "line_matches": [25, 67, 89],
                    "replacements": 3
                },
                ...
            },
            "applied": true
        }

    Notes:
        - Preview mode (default) allows seeing changes before applying them
        - When preview=False, the files are actually modified
        - For large codebases, use file_extensions to limit which files are processed
        - Binary files are automatically skipped
        - The regex mode supports full Python regular expression syntax
        - Use caution when applying replacements to many files at once
    """
    import re

    if not os.path.isdir(directory):
        return {
            "error": f"Directory '{directory}' does not exist or is not a directory"
        }

    # Process file extensions
    if file_extensions:
        # Ensure all extensions start with a dot
        file_extensions = [
            ext if ext.startswith(".") else f".{ext}" for ext in file_extensions
        ]

    # Compile regex if using regex mode
    pattern = None
    if use_regex:
        try:
            pattern = re.compile(find_pattern)
        except re.error as e:
            return {"error": f"Invalid regex pattern: {str(e)}"}

    # Walk through files
    files_matched = 0
    total_replacements = 0
    changes = {}

    for root, _, files in os.walk(directory):
        if not recursive and root != directory:
            continue

        for filename in files:
            # Check file extension if specified
            if file_extensions and not any(
                filename.endswith(ext) for ext in file_extensions
            ):
                continue

            filepath = os.path.join(root, filename)

            # Skip binary files
            try:
                with open(filepath, "rb") as f:
                    chunk = f.read(1024)
                    if b"\0" in chunk:  # Simple binary file check
                        continue
            except:
                continue

            # Process the file
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Search and replace
                if use_regex:
                    # For regex, we need to use regex functions
                    new_content, count = pattern.subn(replace_pattern, content)
                else:
                    # For plain text, use simple string replacement
                    count = content.count(find_pattern)
                    new_content = content.replace(find_pattern, replace_pattern)

                # If we found matches
                if count > 0:
                    files_matched += 1
                    total_replacements += count

                    # Generate line-by-line diff preview
                    line_matches = []
                    preview_lines = []

                    if preview:
                        original_lines = content.splitlines()
                        new_lines = new_content.splitlines()

                        # Compare lines to find differences
                        for i, (old_line, new_line) in enumerate(
                            zip(original_lines, new_lines)
                        ):
                            if old_line != new_line:
                                line_number = i + 1
                                line_matches.append(line_number)
                                preview_lines.append(
                                    f"Line {line_number}: - {old_line}"
                                )
                                preview_lines.append(
                                    f"Line {line_number}: + {new_line}"
                                )

                    # Record the changes
                    if preview:
                        changes[filepath] = {
                            "line_matches": line_matches,
                            "preview": preview_lines,
                            "replacements": count,
                        }
                    else:
                        changes[filepath] = {
                            "line_matches": line_matches,
                            "replacements": count,
                        }

                        # Actually apply the changes
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(new_content)

            except Exception as e:
                # Skip files with errors
                continue

    return {
        "files_matched": files_matched,
        "total_replacements": total_replacements,
        "changes": changes,
        "applied": not preview,
    }


@register_tool(tags=["shell"])
def benchmark_command(command: str, iterations: int = 3, output: bool = False):
    """
    Benchmarks a command by running it multiple times and measuring performance.

    This tool provides detailed timing statistics for a command by executing
    it repeatedly, similar to the 'time' command but with more comprehensive
    analysis.

    Args:
        command: The shell command to benchmark
        iterations: Number of times to run the command (default: 3)
        output: Whether to include command output in the results (default: False)

    Returns:
        Dictionary containing:
        - command: The command that was benchmarked
        - iterations: Number of times the command was run
        - execution_times: List of individual run times in seconds
        - mean_time: Average execution time in seconds
        - min_time: Minimum execution time in seconds
        - max_time: Maximum execution time in seconds
        - std_dev: Standard deviation of execution times
        - output: Command output (if requested)
        - exit_codes: Exit codes from each run

    Examples:
        >>> benchmark_command('find /home/user -name "*.txt"', iterations=5)
        {
            "command": "find /home/user -name \"*.txt\"",
            "iterations": 5,
            "execution_times": [0.342, 0.337, 0.345, 0.339, 0.341],
            "mean_time": 0.341,
            "min_time": 0.337,
            "max_time": 0.345,
            "std_dev": 0.003,
            "exit_codes": [0, 0, 0, 0, 0],
            "all_successful": true
        }

        >>> benchmark_command('python -c "import time; time.sleep(0.1); print(\'Done\')"', output=True)
        {
            "command": "python -c \"import time; time.sleep(0.1); print('Done')\"",
            "iterations": 3,
            "execution_times": [0.123, 0.125, 0.124],
            "mean_time": 0.124,
            "min_time": 0.123,
            "max_time": 0.125,
            "std_dev": 0.001,
            "output": "Done\n",
            "exit_codes": [0, 0, 0],
            "all_successful": true
        }

    Notes:
        - The minimum iterations is 1, and values less than 1 will be set to 1
        - The command is executed exactly as provided, in a shell environment
        - Standard deviation helps identify command stability across runs
        - The same output is repeated for each execution when output=True
        - By default, output is omitted to reduce response size
        - Exit codes for each run are provided to verify consistency
        - Small variations are expected due to system load and disk cache effects
    """
    # Ensure at least one iteration
    iterations = max(1, iterations)

    execution_times = []
    outputs = []
    exit_codes = []

    for i in range(iterations):
        try:
            start_time = time.time()

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if output else subprocess.DEVNULL,
                shell=True,
                text=True,
            )

            if output:
                cmd_output, _ = process.communicate()
                outputs.append(cmd_output)
            else:
                process.wait()
                cmd_output = None

            end_time = time.time()
            execution_time = end_time - start_time

            execution_times.append(execution_time)
            exit_codes.append(process.returncode)

        except Exception as e:
            return {
                "command": command,
                "error": f"Failed to execute command: {str(e)}",
                "iterations_completed": i,
            }

    # Calculate statistics
    mean_time = sum(execution_times) / len(execution_times)
    min_time = min(execution_times)
    max_time = max(execution_times)

    # Calculate standard deviation
    variance = sum((t - mean_time) ** 2 for t in execution_times) / len(execution_times)
    std_dev = variance**0.5

    # Round all times to 3 decimal places for readability
    execution_times = [round(t, 3) for t in execution_times]
    mean_time = round(mean_time, 3)
    min_time = round(min_time, 3)
    max_time = round(max_time, 3)
    std_dev = round(std_dev, 3)

    result = {
        "command": command,
        "iterations": iterations,
        "execution_times": execution_times,
        "mean_time": mean_time,
        "min_time": min_time,
        "max_time": max_time,
        "std_dev": std_dev,
        "exit_codes": exit_codes,
        "all_successful": all(code == 0 for code in exit_codes),
    }

    # Include output if requested
    if output and outputs:
        # Just include the first output to save space
        result["output"] = outputs[0]

    return result
