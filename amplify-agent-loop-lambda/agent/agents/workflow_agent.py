import json

from agent.capabilities.common_capabilities import (
    PassResultsCapability,
    TimeAwareCapability,
    PlanFirstCapability,
    CurrentUserAwareCapability,
)
from agent.capabilities.workflow_capability import WorkflowCapability
from agent.capabilities.workflow_model import Workflow
from agent.components.common_goals import (
    CAREFUL_ARGUMENT_SELECTION,
    ALLOW_EARLY_EXIT_AGENT_LOOP,
)
from agent.components.agent_languages import (
    AgentFunctionCallingActionLanguage,
    AgentJsonActionLanguage,
)
from agent.components.python_action_registry import PythonActionRegistry
from agent.components.python_environment import PythonEnvironment
from agent.components.tool import register_tool
from agent.core import Agent, Environment, ActionRegistry, Goal, Memory
from agent.prompt import create_llm
from agent.tools.prompt_tools import prompt_llm_with_messages


# sample_workflow = [
#     # {
#     #     "tool":"think",
#     #     "instructions":"Think about scheduling",
#     #     "values":{"what_to_think_about":"""
#     #
#     #     Stop and think step by step.
#     #
#     #     Remember, these are emails sent TO the current user and you are scheduling on their behalf.
#     #
#     #     Let's start by simply providing a concrete list of the constraints for the scheduling problem.
#     #
#     #     ## The user's email:
#     #     <Insert the user's email>
#     #
#     #     ## Who sent the email to the user (look at the original sender if it was forwarded):
#     #     <Insert the name and email of the person who sent the email>
#     #
#     #     ## The date / time for the user:
#     #     <Insert the date / time for the user in Day of Week, Time of Day, Day of Month, Month of Year, Year format with time zone>
#     #
#     #     ## We have the following constraints:
#     #     1. We don't schedule meetings outside of work hours, unless it is a dinner or breakfast meeting. If so, 7am is the earliest and 7pm is the latest.
#     #     2. We don't schedule meetings on weekends.
#     #     3. We don't schedule meetings on holidays.
#     #     4. We pay attention to and try to honor any preferences of the user or what the other person stated in their email.
#     #     5. Unless specifically requested, we can't schedule a meeting with less than 24 hours notice. If the meeting
#     #        must be the same day, then we try to give at least 2 hours notice.
#     #     6. We try to offer time slots across at least 2-3 different days. We round to the nearest 15mins.
#     #
#     #     ## The requested dates / times or general time frame for the meeting:
#     #     <Insert the requested time frame for the meeting and the time zone, if mentioned | NONE>
#     #
#     #     ## The people involved in the meeting:
#     #     <Insert the people involved in the meeting and their emails (if available) | NONE>
#     #
#     #     ## Are there scheduling constraints / preferences listed in the email?
#     #     <Insert the constraints / scheduling preferences from the email | NONE>
#     #
#     #     NOW STOP!
#     #     """},
#     #     "args": {
#     #         "what_to_think_about": "leave blank",
#     #     }
#     # },
#     {
#         "instructions":"Lookup the availability of the user between the earliest / latest dates. Try to schedule with at least 24hrs advance notice.",
#         "tool": "getFreeTimeSlots",
#         "args": {}
#     },
#     {
#         "instructions":"""
#
#         Pay attention to and try to honor preferences / constraints on scheduling from the email and the user.
#         Avoid holidays.
#
#         Stop and think step by step. What dates / times work for the user based on the availability you found,
#         the user's constraints, and the constraints from the email.
#
#         Remember, we are scheduling the meeting on behalf of the user.
#
#         We need to send an email from the current user to the other people. Address the email to the person
#         that emailed the user.
#
#         Provide a nicely formatted reply email to the original email but DO NOT use markdown. Use plain text.
#
#         Make sure the email is to the person who sent the email to the user and that it includes the times that work for the user.
#
#         Stop and think step by step.
#         """,
#         "tool": "composeEmailDraft",
#         "args": {
#             "to": "<fill in the emails separated by commas (NOT the user)>",
#             "body": "<Provide an email with the list of options that work>",
#         }
#     },
#     {
#         "stepName": "done",
#         "tool": "terminate",
#         "instructions": "Terminate the conversation and list the proposed times in the message.",
#         "args": {
#             "message": "<fill in with the proposed dates / times>",
#         }
#     },
# ]


def load_workflow(file_path):
    with open(file_path, "r") as f:
        content = f.read()
        steps = json.loads(content)
        return Workflow.from_steps(steps, "workflow")


@register_tool(tags=["workflow"])
def think(action_context, _memory: Memory, what_to_think_about: str):
    """
    Stop and think step by step.

    :param message:
    :return:
    """

    messages = AgentFunctionCallingActionLanguage().format_memory(_memory)

    thoughts = prompt_llm_with_messages(
        action_context=action_context,
        prompt=[
            {
                "role": "system",
                "content": "Stop and think step by step. Be very careful and detailed in your thinking. Be concrete and specific.",
            },
            *messages,
            {"role": "user", "content": what_to_think_about},
        ],
    )

    return thoughts


def build_python_agent(
    workflow_file,
    model="gpt-4o-mini",
    access_token=None,
    current_user="Agent",
    additional_goals=None,
):

    generate_response = create_llm(access_token, model, current_user)

    workflow = load_workflow(workflow_file)

    return build_clean(
        PythonEnvironment(),
        PythonActionRegistry(),
        generate_response,
        workflow,
        additional_goals,
    )


def build_clean(
    environment: Environment,
    action_registry: ActionRegistry,
    generate_response,
    workflow,
    additional_goals=None,
):
    """
    Initialize the base agent with initial actions and goals
    """
    additional_goals = additional_goals or []

    goals = [
        Goal(
            name="REQUIRED", description="YOU ARE REQUIRED TO CALL A TOOL EVERY TIME!"
        ),
        CAREFUL_ARGUMENT_SELECTION,
        ALLOW_EARLY_EXIT_AGENT_LOOP,
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
            CurrentUserAwareCapability(),
            WorkflowCapability(workflow=workflow),
        ],
    )

    return agent
