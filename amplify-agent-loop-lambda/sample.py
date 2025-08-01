import asyncio
import json

from agent.core2 import Agent, AgentContext
from agent.capabilities.remember_results import RememberResultsCapability
from agent.capabilities.use_tools import ToolUseCapability
from agent.environments.python_function_calling import PythonFunctionEnvironment
from agent.tools.python_action_registry import ActionRegistry
from agent.llm_service import LiteLLMService

import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec


async def main():

    def print_agent_decisions(event_type, evt):
        if event_type == "agent/response/received":
            print(f"Agent: {evt['response']}")

    agent_context = AgentContext()
    agent_context.add_listener(print_agent_decisions)

    action_registry = ActionRegistry()

    my_agent = Agent(
        llm_service=LiteLLMService(),
        goals=[
            """"
                You are Action Agent, a helpful AI assistant.

                Your goal is to accomplish the task given by the user. If you have enough information to directly 
                    respond to the user's request, you should do so. If you need more information, you can use tools
                    to help you. If you need to complete tasks, you can use the provided tools to help you. Whenever you are
                    completely done with the task, you should tell the user the result and terminate the conversation.

                For reasoning tasks, such as summarization, inference, planning, classification, writing, etc., you should
                perform the task directly after acquiring the necessary information.
            """
        ],
        capabilities=[
            ToolUseCapability(action_registry=action_registry),
            RememberResultsCapability(),
        ],
        environment=PythonFunctionEnvironment(action_registry=action_registry),
    )
    result = await my_agent.run(
        context=agent_context,
        initial_input="Write a long report on Vanderbilt with two sections and no subsections",
    )
    print(result)


# Entry point for the script
if __name__ == "__main__":
    asyncio.run(main())
