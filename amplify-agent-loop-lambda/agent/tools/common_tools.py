from typing import Dict, List

from agent.components.tool import register_tool


# @register_tool()
def get_user_input(message: str) -> str:
    """
    Get user input based on the provided message.

    Parameters:
        message (str): The prompt to display to the user.

    Returns:
        str: The user's input.
    """
    return input(message)


@register_tool(terminal=True)
def terminate(message: str, result_references: List = None):
    """
    Terminate the conversation.
    No other actions can be run after this one.
    This must be run when the task is complete.

    If you need to return some specific results, refer to them using the result_references parameter
    and they will be included.

    This tool ONLY outputs a message to the user and ends execution. No code you output with it will be
    run. If the user asked for a file, make sure you save the file with code

    When you terminate, include results that are important for the user to see based on their original
    request. This could be the output of a calculation, the result of a web request, or any other
    that answers the user's question or completes the task. The message should explain the results if
    any are included.

    Returns:
        dict: The message to display to the user and the results of the actions in the
    """

    return {"message": message, "results": result_references}
