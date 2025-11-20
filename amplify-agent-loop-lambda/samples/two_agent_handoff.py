from agent.agents import actions_agent, summarizer_agent
from agent.components.agent_registry import AgentRegistry
from agent.components.python_action_registry import PythonActionRegistry
from agent.components.python_environment import PythonEnvironment
from agent.components.util import event_printer
from agent.core import Goal
from agent.prompt import create_llm
import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec
import agent.tools.workflow
import agent.tools.prompt_tools
import agent.tools.file_handling
import agent.tools.agent_communication
import agent.agents.summarizer_agent
from agent.prompt import generate_response

if __name__ == "__main__":

    agent = actions_agent.build_python_agent(
        model="gpt-4o-mini",
        additional_goals=[
            Goal(
                name="CRITICAL OVERRIDE!!!",
                description=""""
                Ignore the terminate command.
            
                EVERY CONVERSATION MUST END WITH A HAND-OFF TO THE SUMMARIZER AGENT.
                When any needed actions are complete, hand-off to the Summarizer Agent to explain the steps taken and the result.
                
                Don't terminate the conversation yourself. Instead, hand-off to the Summarizer Agent.
                """,
            )
        ],
    )

    summarizer = summarizer_agent.build(agent.environment, generate_response)

    agent_registry = AgentRegistry()
    agent_registry.register(
        "Action Agent", "Can use tools to take actions on behalf of the user.", agent
    )
    agent_registry.register(
        "Summarizer Agent", "Can summarize the conversation and results.", summarizer
    )

    action_context_props = {
        "event_handler": event_printer,
        "agent_registry": agent_registry,
    }

    user_input = input("Enter a prompt for the agent: ")

    result = agent.run(user_input=user_input, action_context_props=action_context_props)
    print(result)
