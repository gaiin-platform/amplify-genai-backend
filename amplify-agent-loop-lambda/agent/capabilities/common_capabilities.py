import json
from typing import List
from zoneinfo import ZoneInfo

from agent.components.util import resolve_references
from agent.core import Capability, ActionContext, Memory, Action
from agent.prompt import Prompt
from agent.tools.planning import create_plan, determine_progress
from datetime import datetime


def get_results_map(agent, action_context, response):
    environment = action_context.get_environment()
    if not environment:
        return {}

    if hasattr(environment, "result_history"):
        result_history = environment.result_history
        if not result_history:
            return {}

        def get_result_value(result):
            result = result.get("result", result.get("error", ""))
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                    return result
                except:
                    return result
            return result

        return {result["id"]: get_result_value(result) for result in result_history}

    return {}


class CurrentUserAwareCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Current User Awareness",
            description="Allows the agent to be aware of the current user",
        )

    def init(self, agent, action_context: ActionContext) -> dict:
        memory = action_context.get_memory()
        current_user = action_context.get("current_user", "UNKNOWN")
        memory.add_memory(
            {
                "type": "system",
                "content": f"THE CURRENT USER IS: {current_user}. Make sure and consider this when responding.",
            }
        )


class PlanFirstCapability(Capability):
    def __init__(self, plan_memory_type="system", track_progress=False):
        super().__init__(
            name="Plan First Capability",
            description="The Agent will always create a plan and add it to the memory at the start",
        )
        self.plan_memory_type = plan_memory_type
        self.first_call = True
        self.track_progress = track_progress

    def init(self, agent, action_context):
        if self.first_call:
            self.first_call = False
            plan = create_plan(
                action_context=action_context,
                _memory=action_context.get_memory(),
                action_registry=action_context.get_action_registry(),
            )

            if plan:
                action_context.get_memory().add_memory(
                    {
                        "type": self.plan_memory_type,
                        "content": "You must follow these instructions carefully to complete the task:\n"
                        + plan,
                    }
                )
            else:
                print("Plan was empty, not adding to memory")

    def process_new_memories(
        self,
        agent,
        action_context: ActionContext,
        memory: Memory,
        response,
        result,
        memories: List[dict],
    ):
        if self.track_progress:
            progress = determine_progress(
                action_context=action_context,
                memory=memory,
                action_registry=action_context.get_action_registry(),
            )

            return memories + [{"type": self.plan_memory_type, "content": progress}]

        return memories


class ResponseResultReferencingCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Referencing",
            description="Allows the agent to reference results in responses",
        )

    def process_response(self, agent, action_context, response):
        return resolve_references(
            response, get_results_map(agent, action_context, response)
        )


class TimeAwareCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Time Awareness", description="Allows the agent to be aware of time"
        )

    def process_prompt(
        self, agent, action_context: ActionContext, prompt: Prompt
    ) -> Prompt:
        return prompt

    def init(self, agent, action_context: ActionContext) -> dict:
        try:
            # Define timezone
            time_zone_name = action_context.get("time_zone", "America/Chicago")
            chicago_tz = ZoneInfo(time_zone_name)

            # Get current time in the specified timezone
            now_chicago = datetime.now(chicago_tz)

            # Format the date
            iso_time = now_chicago.strftime(
                "%Y-%m-%dT%H:%M:%S%z"
            )  # ISO format with timezone offset
            formatted_date = now_chicago.strftime(
                "%H:%M %A, %B %d, %Y"
            )  # Desired format

            print(f"ISO Time: {iso_time}")  # Example: 2025-02-09T01:11:00-0600
            print(
                f"Formatted Date: {formatted_date}"
            )  # Example: 01:11 Friday, February 9, 2025

            print(f"ISO Time: {iso_time}")  # Example: 2025-02-09T01:11:00-0600
            print(
                f"Formatted Date: {formatted_date}"
            )  # Example: 01:11 Friday, February 9, 2025

            memory = action_context.get_memory()

            memory.add_memory(
                {
                    "type": "system",
                    "content": f"Right now, it is {formatted_date} (ISO: {iso_time}). "
                    f"You are in the {time_zone_name} timezone. "
                    f"Please consider the day/time, if relevant, when responding.",
                }
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            print("Error getting time zone, disabling time aware capability", e)


class PassResultsCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Passing",
            description="Allows the agent to pass past results to actions by reference",
        )

    def process_action(
        self, agent, action_context: ActionContext, action_def: Action, action: dict
    ) -> dict:
        if "args" in action:
            action["args"] = resolve_references(
                action["args"], get_results_map(agent, action_context, action)
            )
        return action
