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

