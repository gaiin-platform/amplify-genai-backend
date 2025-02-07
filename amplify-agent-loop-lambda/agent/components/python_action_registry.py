from typing import List

from agent.components import tool
from agent.core import Action, ActionRegistry


class PythonActionRegistry(ActionRegistry):
    def __init__(self, tags: List[str] = None):
        super().__init__()

        if tags:
            for tag in tags:
                for t in tool.tools_by_tag.get(tag, []):
                    self.register(Action(
                        name=t["tool_name"],
                        function=t["function"],
                        description=t["description"],
                        parameters=t.get("args", {}),
                        output=t.get("output", {}),
                        terminal=t.get("terminal", False)
                    ))
        else:
            for tool_name, tool_desc in tool.tools.items():
                self.register(Action(
                    name=tool_name,
                    function=tool_desc["function"],
                    description=tool_desc["description"],
                    parameters=tool_desc.get("args", {}),
                    output=tool_desc.get("output", {}),
                    terminal=tool_desc.get("terminal", False)
                ))