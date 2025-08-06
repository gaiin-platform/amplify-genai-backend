from agent.components.tool import register_tool
from agent.core import ActionContext


@register_tool(tags=["agent_communication"])
def list_available_agents(action_context: ActionContext):
    """
    List all available agents that work can be handed-off to.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing the agent IDs and descriptions.
    """
    registry = action_context.get_agent_registry()
    if registry is None:
        return []

    return action_context.get_agent_registry().get_descriptions()


@register_tool(terminal=True, tags=["agent_communication"])
def handoff_to_agent(
    action_context: ActionContext, agent_id: str, instructions_for_agent: str
) -> str:
    """
    Handoff the conversation to another agent.

    Parameters:
        agent_id (str): The ID of the agent to handoff to.
        instructions_for_agent (str): Instructions to provide to the agent on what to do.

    Returns:
        str: The response from the agent.
    """
    memory = action_context.get_memory().copy_without_system_memories()
    to_agent = action_context.get_agent_registry().get(agent_id)

    memory.add_memory(
        {
            "type": "user",
            "content": f"You are the agent with ID '{agent_id}' and a new task has been handed-off to you."
            f"You can refer to the prior messages for information. ",
        }
    )

    if to_agent:
        try:
            to_agent.run(
                user_input=instructions_for_agent,
                memory=memory,
                action_context_props=action_context.properties,
            )
            return "Agent returned. Check the conversation for its output."
        except Exception as e:
            return f"Error running agent: {str(e)}"
    else:
        return f"Agent with ID {agent_id} not found."
