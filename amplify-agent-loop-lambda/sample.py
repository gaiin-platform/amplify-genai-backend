import asyncio
import json

from agent.core2 import Agent, AgentContext
from agent.capabilities.action_result_memory import RememberActionResultsCapability
from agent.capabilities.tool_use import ToolUseCapability
from agent.environments.python_environment import PythonFunctionEnvironment, ActionRegistry
from agent.llm_service import LiteLLMService

import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec

async def main():

    def print_agent_decisions(event_type, evt):
        if event_type == "agent/response/received":
            print(f"Agent: {evt['response']}")

    agent_context = AgentContext(
        listeners=[print_agent_decisions]
    )

    action_registry = ActionRegistry()

    my_agent = Agent(
        llm_service=LiteLLMService(),
        goals=[
            "Your output should always be long",
            {
                "importance": "Critical!",
                "instructions": "Always call terminate."
            }
        ],
        capabilities=[
            ToolUseCapability(
                action_registry=action_registry
            ),
            RememberActionResultsCapability(),
        ],
        environment=PythonFunctionEnvironment(action_registry=action_registry),
    )
    result = await my_agent.run(context=agent_context, initial_input="Write a long report on Vanderbilt with two sections and no subsections")
    print(result)

# Entry point for the script
if __name__ == "__main__":
    asyncio.run(main())