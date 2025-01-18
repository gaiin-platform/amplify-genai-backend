from typing import List

from agent.tools.python_tool import ActionSpec, tools

class ActionRegistry:
    def __init__(self):
        self.actions = {}

        for tool_name, tool_desc in tools.items():
            self.register(ActionSpec(
                name=tool_name,
                function=tool_desc["function"],
                description=tool_desc["description"],
                args=tool_desc.get("args",{}),
                output=tool_desc.get("output", {}),
                terminal=tool_desc.get("terminal", False),
                examples=tool_desc.get("examples", []),
                side_effects=tool_desc.get("side_effects", []),
                tags=tool_desc.get("tags", [])
            ))

    def register(self, action: ActionSpec):
        self.actions[action.name] = action

    def get_action(self, name: str) -> [ActionSpec,None]:
        return self.actions.get(name, None)

    def get_actions(self) -> List[ActionSpec]:
        """Get all action descriptions for prompt"""
        return [action for action in self.actions.values()]
