from agent.components.tool import register_tool


@register_tool()
def get_user_input(message: str) -> str:
    """
    Get user input based on the provided message.

    Parameters:
        message (str): The prompt to display to the user.

    Returns:
        str: The user's input.
    """
    return input(message)
