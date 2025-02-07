import uuid
from collections.abc import Callable
from typing import Any, Dict, List

from agent import tool


class Action:
    def __init__(self,
                 name: str,
                 function: callable,
                 description: str,
                 parameters: Dict,
                 output: Dict,
                 side_effects: Dict = {},
                 terminal: bool = False):
        self.name = name
        self.function = function
        self.description = description
        self.terminal = terminal
        self.parameters = parameters
        self.output = output
        self.side_effects = side_effects

    def execute(self, **args) -> Any:
        """Execute the action's function"""
        return self.function(**args)

    def todict(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "terminal": self.terminal
        }


class ActionRegistry:
    def __init__(self, tags: List[str] = None):
        self.actions = {}

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

    def register(self, action: Action):
        self.actions[action.name] = action

    def get_action(self, name: str) -> [Action,None]:
        return self.actions.get(name, None)

    def get_actions(self) -> List[Action]:
        """Get all action descriptions for prompt"""
        return [action for action in self.actions.values()]


class ActionContext:
    def __init__(self, properties: Dict=None):
        self.context_id = str(uuid.uuid4())
        self.properties = properties or {}

    def enable_code_exec_tool_calls(self, action_registry):
        self.properties["code_exec_context"] = {
            **{k: v.function for k, v in action_registry.actions.items()}
        }

    def get(self, key: str, default=None):
        return self.properties.get(key, default)

    def set(self, key: str, value: Any):
        self.properties[key] = value

    def get_environment(self):
        return self.properties.get("environment", None)

    def get_action_registry(self):
        return self.properties.get("action_registry", None)

    def get_agent_registry(self):
        return self.properties.get("agent_registry", None)

    def get_memory(self):
        return self.properties.get("memory", None)

    def send_event(self, event_id: str, event: Dict):
        hdlr = self.properties.get("event_handler", None)
        if not isinstance(event, dict):
            event = {"content": event}
        if hdlr and callable(hdlr) and event:
            event["context_id"] = self.context_id
            hdlr(event_id, event)

    def incremental_event(self, event = None) -> callable:
        base_props = event or {}
        # Create a correlation ID for the event with a uuid
        correlation_id = str(uuid.uuid4())

        def handler(event_id: str, event: Dict) -> str:
            new_event = event or {}
            self.send_event(event_id, {**base_props, **new_event, "correlation_id": correlation_id})

        return handler