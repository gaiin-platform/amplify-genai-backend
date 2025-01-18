import json
from typing import List
from agent.core import AgentResponse, Memory, ActionResult, NO_ACTION


class RememberActionResultsCapability:
    """Let's the agent remember the result of its actions."""

    def __init__(self):
        """
        """
        pass

    def get_result_str(self, action_result: ActionResult) -> str:

        if isinstance(action_result.result, dict) and 'message' in action_result.result:
            return action_result.result['message']
        elif isinstance(action_result.result, dict):
            return json.dumps(action_result.result, indent=4)
        else:
            return str(action_result.result)


    def process_memories(self, memory_state: List[Memory], response: AgentResponse, action_result: ActionResult) -> List[Memory]:
        """
        Update memories based on the tool execution result.
        :param memory_state: Current list of memories.
        :param action_result: Result of the action.
        :return: Updated list of memories.
        """

        if response and action_result:

            new_memories = []

            new_memories.append(Memory(
                content=response.raw_response,
                memory_type="assistant",
                metadata={"type": "action"}
            ))

            if response.parsed_response and response.parsed_response != NO_ACTION:
                result_str = self.get_result_str(action_result)

                new_memories.append(Memory(
                    content=result_str,
                    memory_type="user",
                    metadata={"type": "action_result"}
                ))

            return memory_state + new_memories

        else:
            return memory_state
