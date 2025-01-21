import os

from agent.tool import register_tool


@register_tool()
def get_current_directory(action_context):
    wd = action_context.get('working_directory')
    return wd

@register_tool()
def list_files_in_directory(directory_absolute_path: str):
    return os.listdir(directory_absolute_path)

