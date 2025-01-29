import json
import time

from agent.core import Capability
from agent.game.action import ActionContext
from agent.prompt import Prompt
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