from agent.agents.common_capabilities import PassResultsCapability, ResponseResultReferencingCapability, \
    TimeAwareCapability
from agent.game.action import ActionRegistry
from agent.game.environment import Environment
from agent.game.goal import Goal, BE_DIRECT, LARGE_RESULTS, PREFER_WORKFLOWS, USE_RESULT_REFERENCES_IN_RESPONSES, \
    PASS_RESULT_REFERENCES_TO_TOOLS
from agent.game.languages import AgentJsonActionLanguage, AgentFunctionCallingActionLanguage
from agent.core import Agent


def build(environment: Environment, action_registry: ActionRegistry, generate_response, additional_goals=None):
    """
    Initialize the base agent with initial actions and goals
    """
    additional_goals = additional_goals or []

    goals = [
        Goal(
            name="Persona",
            description="You are Action Agent, a helpful AI assistant."
        ),
        Goal(
            name="Accomplish user tasks",
            description="""
Your goal is to accomplish the task given by the user. If you have enough information to directly 
respond to the user's request, you should do so. If you need more information, you can use tools
to help you. If you need to complete tasks, you can use the provided tools to help you. Whenever you are
completely done with the task, you should tell the user the result and terminate the conversation.
"""
        ),
        BE_DIRECT,
        LARGE_RESULTS,
        PREFER_WORKFLOWS,
        USE_RESULT_REFERENCES_IN_RESPONSES,
        PASS_RESULT_REFERENCES_TO_TOOLS,
        *additional_goals
    ]

    agent = Agent(
        goals=goals,
        agent_language=AgentFunctionCallingActionLanguage(),
        action_registry=action_registry,
        generate_response=generate_response,
        environment=environment,
        capabilities=[
            TimeAwareCapability(),
            PassResultsCapability()
        ]
    )

    return agent

