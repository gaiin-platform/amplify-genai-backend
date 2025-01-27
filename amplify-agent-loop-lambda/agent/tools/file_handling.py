import os

from agent.tool import register_tool


@register_tool()
def get_current_directory():
    return os.getcwd()

@register_tool()
def list_files_in_directory(directory: str):
    return os.listdir(directory)

