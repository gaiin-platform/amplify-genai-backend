from agent.capabilities.common_capabilities import (
    PassResultsCapability,
    TimeAwareCapability,
    PlanFirstCapability,
)
from agent.components.common_goals import (
    BE_DIRECT,
    LARGE_RESULTS,
    PREFER_WORKFLOWS,
    USE_RESULT_REFERENCES_IN_RESPONSES,
    PASS_RESULT_REFERENCES_TO_TOOLS,
    BAIL_OUT_ON_MANY_ERRORS,
    STOP_WHEN_STUCK,
)
from agent.components.agent_languages import (
    AgentFunctionCallingActionLanguage,
    AgentJsonActionLanguage,
)
from agent.components.python_action_registry import PythonActionRegistry
from agent.components.python_environment import PythonEnvironment
from agent.core import Agent, Environment, ActionRegistry, Goal
from agent.prompt import create_llm


def build_python_agent(model="gpt-4o-mini", additional_goals=None):
    generate_response = create_llm(None, model)
    return build_clean(
        PythonEnvironment(), PythonActionRegistry(), generate_response, additional_goals
    )


def build_clean(
    environment: Environment,
    action_registry: ActionRegistry,
    generate_response,
    additional_goals=None,
):
    """
    Initialize the base agent with initial actions and goals
    """
    additional_goals = additional_goals or []

    goals = [*additional_goals]

    agent = Agent(
        goals=goals,
        agent_language=AgentFunctionCallingActionLanguage(),
        action_registry=action_registry,
        generate_response=generate_response,
        environment=environment,
        capabilities=[
            TimeAwareCapability(),
            PassResultsCapability(),
            PlanFirstCapability(),
        ],
    )

    return agent


def build(
    environment: Environment,
    action_registry: ActionRegistry,
    generate_response,
    additional_goals=None,
):
    """
    Initialize the base agent with initial actions and goals
    """
    additional_goals = additional_goals or []

    goals = [
        Goal(
            name="Persona", description="You are Action Agent, a helpful AI assistant."
        ),
        Goal(
            name="Accomplish user tasks",
            description="""
Your goal is to accomplish the task given by the user. If you have enough information to directly 
respond to the user's request, you should do so. If you need more information, you can use tools
to help you. If you need to complete tasks, you can use the provided tools to help you. Whenever you are
completely done with the task, you should tell the user the result and terminate the conversation.
""",
        ),
        BE_DIRECT,
        LARGE_RESULTS,
        PREFER_WORKFLOWS,
        USE_RESULT_REFERENCES_IN_RESPONSES,
        PASS_RESULT_REFERENCES_TO_TOOLS,
        BAIL_OUT_ON_MANY_ERRORS,
        STOP_WHEN_STUCK,
        *additional_goals,
    ]

    agent = Agent(
        goals=goals,
        agent_language=AgentFunctionCallingActionLanguage(),
        action_registry=action_registry,
        generate_response=generate_response,
        environment=environment,
        capabilities=[
            TimeAwareCapability(),
            PassResultsCapability(),
            PlanFirstCapability(),
        ],
    )

    return agent
