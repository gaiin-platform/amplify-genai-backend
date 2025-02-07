import json
import time
from typing import List

from agent.core import Capability
from agent.game.action import ActionContext
from agent.game.memory import Memory
from agent.prompt import Prompt
from agent.tools.planning import create_plan, determine_progress
from agent.util import resolve_references


def get_results_map(agent, action_context, response):
    environment = action_context.get_environment()
    if not environment:
        return {}

    if hasattr(environment, "result_history"):
        result_history = environment.result_history
        if not result_history:
            return {}

        def get_result_value(result):
            result = result.get('result',result.get('error', ''))
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                    return result
                except:
                    return result
            return result

        return {result["id"]: get_result_value(result) for result in result_history}

    return {}


class PlanFirstCapability(Capability):
    def __init__(self, plan_memory_type="user", track_progress=True):
        super().__init__(
            name="Plan First Capability",
            description="The Agent will always create a plan and add it to the memory at the start"
        )
        self.plan_memory_type = plan_memory_type
        self.first_call = True
        self.track_progress = track_progress

    def init(self, agent, action_context):
        if self.first_call:
            self.first_call = False
            plan = create_plan(action_context=action_context,
                        memory=action_context.get_memory(),
                        action_registry=action_context.get_action_registry())

            action_context.get_memory().add_memory({
                "type": self.plan_memory_type,
                "content": "YOUR PLAN:\n" + plan
            })

    def process_new_memories(self, agent, action_context: ActionContext, memory: Memory, response, result, memories: List[dict]):
        if self.track_progress:
            progress = determine_progress(action_context=action_context,
                        memory=memory,
                        action_registry=action_context.get_action_registry())

            return memories + [{
                "type": self.plan_memory_type,
                "content": progress
            }]

        return memories

class ResponseResultReferencingCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Referencing",
            description="Allows the agent to reference results in responses"
        )

    def process_response(self, agent, action_context, response):
        return resolve_references(response, get_results_map(agent, action_context, response))


class TimeAwareCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Time Awareness",
            description="Allows the agent to be aware of time"
        )

    def process_prompt(self, agent, action_context: ActionContext, prompt: Prompt) -> Prompt:
        iso_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        prompt.messages = [{
            "role": "system",
            "content": f"The current time is {iso_time}. Please consider the day/time, if relevant, when responding."
        }] + prompt.messages

        return prompt

class PassResultsCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Passing",
            description="Allows the agent to pass past results to actions by reference"
        )

    def process_action(self, agent, action_context: ActionContext, action: dict) -> dict:
        action["args"] = resolve_references(action["args"], get_results_map(agent, action_context, action))
        return action