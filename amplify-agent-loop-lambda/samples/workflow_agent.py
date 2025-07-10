import os

from agent.agents import workflow_agent
from agent.components.util import event_printer
from agent.core import Goal
import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec
import agent.tools.workflow
import agent.tools.prompt_tools
import agent.tools.file_handling
import agent.tools.agent_communication
import agent.agents.summarizer_agent
from agent.tools.ops import register_op_actions
from service.conversations import register_agent_conversation

if __name__ == "__main__":

    if not os.getenv("ACCESS_TOKEN") or not os.getenv("CURRENT_USER"):
        raise Exception(
            "Unable to fetch APIs. Check that the ACCESS_TOKEN has not expired and CURRENT_USER is set."
        )
    access_token = os.getenv("ACCESS_TOKEN")
    current_user = os.getenv("CURRENT_USER")

    workflow_file = "workflows/schedule.json"

    agent = workflow_agent.build_python_agent(
        workflow_file,
        model="gpt-4o-mini",
        access_token=access_token,
        current_user=current_user,
        additional_goals=[
            Goal(
                name="Always Choose a Tool",
                description="""
            
            Stop and think step by step. Do I have a tool available to me? If so, YOU MUST USE IT.
            
            Make sure that you always choose a tool. Even if you don't think you know what to do, you always
            must make choose one of the tools available to you. Trust in the process, the tools are being
            presented to you for a reason. Choose one and use its description and parameter descriptions to
            figure out how to use it. Look at the conversation to find the answers to input into the tool.
            
            YOU MUST CHOOSE A TOOL.
            """,
            )
        ],
    )

    register_op_actions(agent.actions, access_token, current_user)

    action_context_props = {
        "event_handler": event_printer,
        "access_token": access_token,
        "current_user": current_user,
    }

    user_input = input("Enter a prompt for the agent: ")

    result = agent.run(user_input=user_input, action_context_props=action_context_props)

    register_agent_conversation(
        access_token=access_token, input=user_input, memory=result
    )

    print(result)
