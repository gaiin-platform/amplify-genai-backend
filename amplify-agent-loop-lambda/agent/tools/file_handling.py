import os

from agent.components.tool import register_tool


@register_tool()
def get_current_directory(action_context):
    wd = action_context.get('work_directory')
    return wd


@register_tool()
def list_files_in_directory(directory: str):
    return os.listdir(directory)

