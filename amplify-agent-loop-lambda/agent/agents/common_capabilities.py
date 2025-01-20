from agent.core import Capability
from agent.game.action import ActionContext
from agent.util import resolve_references


def get_results_map(agent, action_context, response):
    environment = action_context.get_environment()
    if not environment:
        return {}

    if hasattr(environment, "result_history"):
        result_history = environment.result_history
        if not result_history:
            return {}

        return {result["id"]: result.get('result',result.get('error', '')) for result in result_history}

    return {}


class ResponseResultReferencingCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Referencing",
            description="Allows the agent to reference results in responses"
        )

    def process_response(self, agent, action_context, response):
        return resolve_references(response, get_results_map(agent, action_context, response))


class PassResultsCapability(Capability):
    def __init__(self):
        super().__init__(
            name="Result Passing",
            description="Allows the agent to pass past results to actions by reference"
        )

    def process_action(self, agent, action_context: ActionContext, action: dict) -> dict:
        action["args"] = resolve_references(action["args"], get_results_map(agent, action_context, action))
        return action